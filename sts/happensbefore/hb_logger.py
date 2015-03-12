from _collections import defaultdict
import logging

from pox.lib.revent.revent import EventMixin
from sts.happensbefore.hb_sts_events import *
from sts.happensbefore.hb_tags import ObjectRegistry
from sts.happensbefore.hb_events import *
from pox.openflow.libopenflow_01 import *
from sts.util.convenience import base64_encode
from sts.util.procutils import prefixThreadOutputMatcher, PrefixThreadLineMatch
from sts.happensbefore.hb_graph import HappensBeforeGraph

class HappensBeforeLogger(EventMixin):
  '''
  Listens to and logs the following events:
  - Data plane:
   * receive dataplane (switches)
   * receive dataplane (hosts)
   * send dataplane (hosts+switches)
  - Control plane:
   * receive Openflow msgs (controller to switch)
   * send Openflow msgs (switch to controller)
  - Switch:
   * internal processing
  
  Logs the following operations:
   * Flow table read
   * Flow table touch
   * Flow table modify
   '''
  
  controller_hb_msg_in = "HappensBefore-MessageIn"
  controller_hb_msg_out = "HappensBefore-MessageOut"
  
  def __init__(self, patch_panel):
    self.log = logging.getLogger("hb_logger")

    self.hb_graph = None

    self.output = None
    self.output_path = ""
    self.patch_panel = patch_panel
    
    # State for linking of events
    self.pids = ObjectRegistry() # packet obj -> pid
    self.mids = ObjectRegistry() # message obj -> mid
    self.started_switch_event = dict() # dpid -> event
    self.started_host_event = dict() # hid -> event
    self.new_switch_events = defaultdict(list)
    self.new_host_events = defaultdict(list)
    self.pending_packet_update = dict() # dpid -> packet
    
    # State for linking of controller events
    self.unmatched_HbMessageSend = defaultdict(list) # dpid -> []
    self.unmatched_HbMessageHandle = defaultdict(list) # dpid -> []
    
    self.controller_msgin_to_mid_out = dict() # (swid, b64msg) -> mid_out
    self.unmatched_lines_controller_msgout = [] # (in_swid, in_b64msg, out_swid, out_b64msg)
    
    self.swid_to_dpid = dict()
    self.dpid_to_swid = dict()
    
    prefixThreadOutputMatcher.add_string_to_match(self.controller_hb_msg_in)
    prefixThreadOutputMatcher.add_string_to_match(self.controller_hb_msg_out)
    prefixThreadOutputMatcher.addListener(PrefixThreadLineMatch, self._handle_line_match)

    self._subscribe_to_PatchPanel(patch_panel)

    
  def open(self, results_dir=None, output_filename="hb.json"):
    '''
    Start a trace
    '''
    if results_dir is not None:
      self.output_path = results_dir + "/" + output_filename
    else:
      raise ValueError("Default results_dir currently not supported")
    self.hb_graph = HappensBeforeGraph(results_dir)
    self.output = open(self.output_path, 'w')
    
  def close(self):
    '''
    End a trace
    '''
    # Flush the log
    self.output.close()
    self.output = None
  
  def write(self,msg):
    self.log.info(msg)
    if not self.output:
      raise Exception("Not opened -- call HappensBeforeLogger.open()")
    if not self.output.closed:
      self.output.write(str(msg) + '\n')
      self.output.flush()
    if self.hb_graph is not None:
      self.hb_graph.add_line(str(msg))
  
  def handle_no_exceptions(self, event):
    """ Handle event, catch exceptions before they go back to STS/POX
    """
    try:
      if self.output is not None:
        event_handlers = {
            TraceHostPacketHandleBegin: self.handle_host_ph_begin,
            TraceHostPacketHandleEnd: self.handle_host_ph_end,
            TraceHostPacketSend: self.handle_host_ps,
            TraceSwitchPacketHandleBegin: self.handle_switch_ph_begin,
            TraceSwitchPacketHandleEnd: self.handle_switch_ph_end,
            TraceSwitchMessageHandleBegin: self.handle_switch_mh_begin,
            TraceSwitchMessageHandleEnd: self.handle_switch_mh_end,
            TraceSwitchMessageSend: self.handle_switch_ms,
            TraceSwitchPacketSend: self.handle_switch_ps,
            TraceSwitchFlowTableRead: self.handle_switch_table_read,
            TraceSwitchFlowTableWrite: self.handle_switch_table_write,
            TraceSwitchFlowTableEntryExpiry: self.handle_switch_table_entry_expiry,
            TraceSwitchBufferPut: self.handle_switch_buf_put,
            TraceSwitchBufferGet: self.handle_switch_buf_get,
            TraceSwitchPacketUpdateBegin: self.handle_switch_pu_begin,
            TraceSwitchPacketUpdateEnd: self.handle_switch_pu_end
        }
        handler = None
        if type(event) in event_handlers:
          handler = event_handlers[type(event)]
          handler(event)
    except Exception as e:
      # NOTE JM: do not remove, otherwise exceptions get swallowed by STS
      import traceback
      traceback.print_exc(file=sys.stdout)
  
  def subscribe_to_DeferredOFConnection(self, connection):
    connection.addListener(TraceSwitchMessageSend, self.handle_no_exceptions)
      
  def _subscribe_to_PatchPanel(self, patch_panel):
    for host in patch_panel.hosts:
      host.addListener(TraceHostPacketHandleBegin, self.handle_no_exceptions)
      host.addListener(TraceHostPacketHandleEnd, self.handle_no_exceptions)
      host.addListener(TraceHostPacketSend, self.handle_no_exceptions)
    
    for s in patch_panel.switches:
      s.addListener(TraceSwitchPacketHandleBegin, self.handle_no_exceptions)
      s.addListener(TraceSwitchPacketHandleEnd, self.handle_no_exceptions)
      s.addListener(TraceSwitchMessageHandleBegin, self.handle_no_exceptions)
      s.addListener(TraceSwitchMessageHandleEnd, self.handle_no_exceptions)
      s.addListener(TraceSwitchPacketSend, self.handle_no_exceptions)
      s.addListener(TraceSwitchFlowTableRead, self.handle_no_exceptions)
      s.addListener(TraceSwitchFlowTableWrite, self.handle_no_exceptions)
      s.addListener(TraceSwitchFlowTableEntryExpiry, self.handle_no_exceptions)
      s.addListener(TraceSwitchBufferPut, self.handle_no_exceptions)
      s.addListener(TraceSwitchBufferGet, self.handle_no_exceptions)
      s.addListener(TraceSwitchPacketUpdateBegin, self.handle_no_exceptions)
      s.addListener(TraceSwitchPacketUpdateEnd, self.handle_no_exceptions)
  
  
  def write_event_to_trace(self, event):
    self.write(event.to_json())
  
  #
  # Switch helper functions
  #
  
  def start_switch_event(self,dpid,event):
    for i in self.new_switch_events[dpid]:
      self.write_event_to_trace(i)
    del self.new_switch_events[dpid]
    assert event.dpid not in self.started_switch_event 
    
    self.started_switch_event[event.dpid] = event
  
  def finish_switch_event(self, dpid):
    assert dpid in self.started_switch_event
    
    self.write_event_to_trace(self.started_switch_event[dpid])
    del self.started_switch_event[dpid]
    for i in self.new_switch_events[dpid]:
      self.write_event_to_trace(i)
    del self.new_switch_events[dpid]
    
  def is_switch_event_started(self, dpid):
    return dpid in self.started_switch_event
  
  def add_operation_to_switch_event(self, event):
    if self.is_switch_event_started(event.dpid):
      self.started_switch_event[event.dpid].operations.append(event)
    else:
      # Ignore this operation, as there is no started switch event yet.
      self.log.info("Ignoring switch operation as there is no associated begin event.")

  def add_successor_to_switch_event(self, event, mid_in=None, pid_in=None):
    if self.is_switch_event_started(event.dpid):
      if mid_in is not None:
        self.started_switch_event[event.dpid].mid_out.append(mid_in) # link with latest event
      if pid_in is not None:
        self.started_switch_event[event.dpid].pid_out.append(pid_in) # link with latest event
      self.new_switch_events[event.dpid].append(event) # enqueue event to be output as soon as the end event is reached
    else:
      # Output this operation directly as we missed the preceding event.
      self.log.info("Writing switch event even though there was no associated begin event.")
      self.write_event_to_trace(event)

  #
  # Host helper functions
  #

  def start_host_event(self,hid,event):
    for i in self.new_host_events[hid]:
      self.write_event_to_trace(i)
    del self.new_host_events[hid]
    assert event.hid not in self.started_host_event 
    
    self.started_host_event[event.hid] = event
  def finish_host_event(self, hid):
    assert hid in self.started_host_event
    
    self.write_event_to_trace(self.started_host_event[hid])
    del self.started_host_event[hid]
    for i in self.new_host_events[hid]:
      self.write_event_to_trace(i)
    del self.new_host_events[hid]
    
  def is_host_event_started(self, hid):
    return hid in self.started_host_event
  
  def add_successor_to_host_event(self, event, pid_in=None):
    if self.is_host_event_started(event.hid):
      if pid_in is not None:
        self.started_host_event[event.hid].pid_out.append(pid_in) # link with latest event
      self.new_host_events[event.hid].append(event)
    else:
      # Output this operation directly as we missed the preceding event.
      self.log.info("Writing host event even though there was no associated begin event.")
      self.write_event_to_trace(event)
  
  #
  # Switch events
  #
  
  def handle_switch_ph_begin(self, event):
    pid_in = self.pids.get_tag(event.packet) # matches a pid_out as the Python object ids will be the same
    
    begin_event = HbPacketHandle(pid_in, dpid=event.dpid, packet=event.packet, in_port=event.in_port)
    self.start_switch_event(event.dpid, begin_event)
  
  def handle_switch_ph_end(self, event):
    self.finish_switch_event(event.dpid)
  
  def handle_switch_mh_begin(self, event):
      mid_in = self.mids.get_tag(event.msg) # filled in, but never matches a mid_out. This link will be filled in by controller instrumentation.
      msg_type = event.msg.header_type
      
      msg_flowmod = None if not hasattr(event, 'flow_mod') else event.flow_mod
      
      begin_event = HbMessageHandle(mid_in, msg_type, dpid=event.dpid, controller_id=event.controller_id, msg=event.msg, msg_flowmod=msg_flowmod)
      self.start_switch_event(event.dpid, begin_event)
      
      # match with controller instrumentation
      is_matched = self.match_unmatched_controller_msgout(mid_in, event.dpid, base64_encode(event.msg))
      if not is_matched:
        self.unmatched_HbMessageHandle[event.dpid].append((mid_in, base64_encode(event.msg)))
  
  def handle_switch_mh_end(self, event):
    self.finish_switch_event(event.dpid)
  
  def handle_switch_ms(self, event):
    mid_in = self.mids.new_tag(event.msg) # tag changes here
    mid_out = self.mids.new_tag(event.msg) # filled in, but never matches a mid_in. This link will be filled in by controller instrumentation. 
    msg_type = event.msg.header_type
    
    # event.msg goes to the controller, and we cannot match it there. So we remove it from the ObjectRegistry.
    self.mids.remove_obj(event.msg)
    
    new_event = HbMessageSend(mid_in, mid_out, msg_type, dpid=event.dpid, controller_id=event.controller_id, msg=event.msg)   
    self.add_successor_to_switch_event(new_event, mid_in=mid_in)
    
    # add base64 encoded message to list for controller instrumentation
    # this will always come before the switch has had a chance to write out something, 
    # so no need to check anything here
    self.unmatched_HbMessageSend[event.dpid].append((mid_out, base64_encode(event.msg)))
  
  def handle_switch_ps(self, event):
    pid_in = self.pids.new_tag(event.packet) # tag changes here
    pid_out = self.pids.new_tag(event.packet) # tag changes here
    
    new_event = HbPacketSend(pid_in, pid_out, dpid=event.dpid, packet=event.packet, out_port=event.out_port)
    self.add_successor_to_switch_event(new_event, pid_in=pid_in)

  #
  # Switch operation events
  #

  def handle_switch_table_read(self, event):
    self.add_operation_to_switch_event(event)
    
  def handle_switch_table_write(self, event):
    self.add_operation_to_switch_event(event)
    
  def handle_switch_table_entry_expiry(self, event):
    self.add_operation_to_switch_event(event)
    
  def handle_switch_buf_put(self, event):
    if self.is_switch_event_started(event.dpid):
        assert isinstance(self.started_switch_event[event.dpid], HbPacketHandle)
        # the tag should still be the same, as no successor events should have been added yet
        assert self.pids.get_tag(event.packet) == self.started_switch_event[event.dpid].pid_in
        # generate pid_out for buffer write
        pid_out = self.pids.new_tag(event.packet) # tag changes here
        self.started_switch_event[event.dpid].pid_out.append(pid_out)
    self.add_operation_to_switch_event(event)
    
  def handle_switch_buf_get(self, event):
    if self.is_switch_event_started(event.dpid):
      assert isinstance(self.started_switch_event[event.dpid], HbMessageHandle)
      # update the pid_in of the current event using the packet from the buffer
      pid_in = self.pids.get_tag(event.packet)
      self.started_switch_event[event.dpid].pid_in = pid_in
    self.add_operation_to_switch_event(event)
  
  #
  # Switch bookkeeping operations
  #
  
  def handle_switch_pu_begin(self, event):
    """
    Mark an object in the ObjectRegistry for an update. This will keep the tags even if the Python object id (memory address) changes.
    """
    tag = self.pids.get_tag(event.packet)
    self.pending_packet_update[event.dpid] = tag
    
  def handle_switch_pu_end(self, event):
    """
    Swap out the marked object in the ObjectRegistry with the new one, while keeping the tags the same.
    """
    assert event.dpid in self.pending_packet_update 
    tag = self.pending_packet_update[event.dpid]
    obj = event.packet
    self.pids.replace_obj(tag, obj)
  
  #
  # Host events
  #
  
  def handle_host_ph_begin(self, event):
    pid_in = self.pids.get_tag(event.packet) # matches a pid_out as the Python object ids will be the same
    
    begin_event = HbHostHandle(pid_in, hid=event.hid, packet=event.packet, in_port=event.in_port)
    self.start_host_event(event.hid, begin_event)
    
  def handle_host_ph_end(self, event):
    self.finish_host_event(event.hid)
    
  def handle_host_ps(self, event):
    pid_in = self.pids.new_tag(event.packet) # tag changes here
    pid_out = self.pids.new_tag(event.packet) # tag changes here
    
    new_event = HbHostSend(pid_in, pid_out, hid=event.hid, packet=event.packet, out_port=event.out_port)
    self.add_successor_to_host_event(new_event, pid_in=pid_in)
  
  #
  # Controller instrumentation information
  #
  
  def _handle_line_match(self, event):
    line = event.line
    match = event.match
    
    # Format: match-[data1:data2:....]
    # find end of match
    match_end = line.find(match) + len(match)
    rest_of_line = line[match_end:]
    
    # find start of data
    data_start = rest_of_line.find('[') + 1
    data_end = rest_of_line.find(']')
    
    data_str = rest_of_line[data_start:data_end]
    data = data_str.split(':')
    
    self.log.info("Read data from controller: "+line)

    
    # parse data
    if match == self.controller_hb_msg_in:
      swid = int(data[0])
      b64msg = data[1]
      self.controller_ack_in(swid, b64msg)
      
    if match == self.controller_hb_msg_out:
      in_swid = int(data[0])
      in_b64msg = data[1]
      out_swid = int(data[2])
      out_b64msg = data[3]
      self.controller_ack_out(in_swid, in_b64msg, out_swid, out_b64msg)
  
  def add_controller_hb_edge(self, mid_out, mid_in):
    """
    Add an edge derived from controller instrumentation
    """
    temporary_tag = self.mids.generate_unused_tag()
    event = HbControllerHandle(mid_out, temporary_tag)
    self.write_event_to_trace(event)
    event = HbControllerSend(temporary_tag, mid_in)
    self.write_event_to_trace(event)
    self.log.info("Adding controller edge: mid_out:"+str(mid_out)+" -> mid_in:"+str(mid_in)+".")

  
  def find_controller_packet_in(self, swid, b64msg):
    """
    Return an mid_out for the PACKET_IN event
    Return None if the message hasn't been sent yet (This should never happen!)
    """
    if (swid,b64msg) in self.controller_msgin_to_mid_out:
      mid_out = self.controller_msgin_to_mid_out[(swid,b64msg)]
      return mid_out
    
    dpid = None
    if swid in self.swid_to_dpid:
      dpid = self.swid_to_dpid[swid]
      assert self.dpid_to_swid[dpid] == swid
    
    if dpid is None:
      # this is the first time we've seen swid, need to guess which dpid it is
      # Assumption: We will have at least registered the TraceSwitchMessageSend event
      #             before the controller can print out something.
      # We just go through all dpids with no swids and check if the message is there
      
      for dpid_key in self.unmatched_HbMessageSend.keys():
        if dpid_key not in self.dpid_to_swid:
          # no swid for this dpid yet
          for t in self.unmatched_HbMessageSend[dpid_key]:
            if b64msg == t[1]:
              # this is it, assign mappings
              mid_out = t[0]
              
              self.swid_to_dpid[swid] = dpid_key
              self.dpid_to_swid[dpid_key] = swid
              dpid = dpid_key
              self.unmatched_HbMessageSend[dpid_key].remove(t) # only doing this once, so it's okay
                            
              return mid_out
    else:
      for t in self.unmatched_HbMessageSend[dpid]:
        if b64msg == t[1]:
          mid_out = t[0]
          self.unmatched_HbMessageSend[dpid].remove(t) # only doing this once, so it's okay
          return mid_out
    return None
  
  def find_controller_packet_out(self, swid, b64msg):
    """
    Return an mid_in for the PACKET_OUT/FLOW_MOD/etc. event.
    Return None if the message hasn't been received yet.
    """
    dpid = None
    if swid in self.swid_to_dpid:
      dpid = self.swid_to_dpid[swid]
      assert self.dpid_to_swid[dpid] == swid
    
    if dpid is None:
      # this is the first time we've seen swid, need to guess which dpid it is
      # We just go through all dpids with no swids and check if the message is there
      
      # Note: it might be possible that we cannot find the message as we haven't received it yet.
      
      for dpid_key in self.unmatched_HbMessageHandle.keys():
        if dpid_key not in self.dpid_to_swid:
          # no swid for this dpid yet
          for t in self.unmatched_HbMessageHandle[dpid_key]:
            if b64msg == t[1]:
              # this is it, assign mappings
              mid_in = t[0]
              
              self.swid_to_dpid[swid] = dpid_key
              self.dpid_to_swid[dpid_key] = swid
              dpid = dpid_key
              self.unmatched_HbMessageHandle[dpid_key].remove(t) # only doing this once, so it's okay
                            
              return mid_in
    else:
      for t in self.unmatched_HbMessageHandle[dpid]:
        if b64msg == t[1]:
          mid_in = t[0]
          self.unmatched_HbMessageHandle[dpid].remove(t) # only doing this once, so it's okay
          return mid_in
    return None
    
  def controller_ack_in(self, swid, b64msg):
    """
    swid: Controller assigned switch id. Not necessarily == dpid
    b64msg: Message string in base64
    """
    mid_out = self.find_controller_packet_in(swid, b64msg)
    if mid_out is None:
      # this should never happen, we should have already logged the event (we sent it!)
      assert False
    else:
      # possibly overwrite, we only care about the newest
      self.controller_msgin_to_mid_out[(swid,b64msg)] = mid_out

  def controller_ack_out(self, in_swid, in_b64msg, out_swid, out_b64msg):
    mid_out = self.find_controller_packet_in(in_swid, in_b64msg)
    if mid_out is None:
      # this should never happen, we should have already logged the event (we sent it!)
      assert False
    mid_in = self.find_controller_packet_out(out_swid, out_b64msg)
    if mid_in is None:
      # this can happen if we haven't received the packet yet. We'll add the edge later, when the HbMessageHandle event happens.
      self.unmatched_lines_controller_msgout.append((in_swid, in_b64msg, out_swid, out_b64msg))
    else:
      # add a new HB edge to the trace
      self.add_controller_hb_edge(mid_out, mid_in)
      
  def match_unmatched_controller_msgout(self, mid_in, dpid, out_b64msg):
    """
    Match the HbMessageHandle event to any lines that we have already read.
    """
    # need to find the message for the given mid_in
    # so that:  mid_in == self.find_controller_packet_out(self, out_swid, out_b64msg)
    
    swid = None
    if dpid in self.dpid_to_swid:
      swid = self.dpid_to_swid[dpid]
      assert self.swid_to_dpid[swid] == dpid
    
    matched_line = None
    if swid is None:
      # We have never seen a swid with this dpid.
      # We just go through all the unmatched lines and use the first one that matches out_b64msg
      for line in self.unmatched_lines_controller_msgout:
        lin_swid, lin_b64msg, lout_swid, lout_b64msg = line
        if out_b64msg == lout_b64msg:
          # assign swid mapping
          matched_line = line
          swid = lout_swid
          self.dpid_to_swid[dpid] = swid
          self.swid_to_dpid[swid] = dpid
    if swid is not None:
      if matched_line is None:
        for line in self.unmatched_lines_controller_msgout:
          lin_swid, lin_b64msg, lout_swid, lout_b64msg = line
          if out_b64msg == lout_b64msg:
            # assign swid mapping
            matched_line = line
    if matched_line is not None:
      self.unmatched_lines_controller_msgout.remove(matched_line)
      lin_swid, lin_b64msg, lout_swid, lout_b64msg = matched_line
      # now we can link these events
      mid_out = self.find_controller_packet_in(self, lin_swid, lin_b64msg)
      if mid_out is None:
        # this should never happen, we should have already logged the event (we sent it!)
        assert False
      else:
        # add a new HB edge to the trace
        self.add_controller_hb_edge(mid_out, mid_in)
        return True
    return False
  