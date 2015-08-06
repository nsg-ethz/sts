"""
Events instrumenting STS internals.
"""

from hb_json_event import JsonEvent
from hb_json_event import AttributeCombiningMetaclass
from hb_utils import base64_decode_openflow
from hb_utils import base64_encode
from hb_utils import base64_encode_flow
from hb_utils import base64_encode_flow_list
from hb_utils import base64_encode_flow_table
from hb_utils import decode_flow_table
from hb_utils import decode_flow_mod
from hb_utils import decode_packet
from hb_utils import get_port_no
from hb_utils import ofp_type_to_str


class TraceSwitchEvent(JsonEvent):
  __metaclass__ = AttributeCombiningMetaclass
  _attr_combining_metaclass_args = ["_to_json_attrs"]
  
  #TODO(jm): clean up/remove unused ones, check which ones are actually used in hb_graph and hb_events and remove the ones that are not used.
  _to_json_attrs = ['dpid',
                    'controller_id', # socket.getpeername(), NOT the STS cid
                    'hid',
                    ('packet', base64_encode),
                    ('in_port', get_port_no),
                    ('out_port', get_port_no),
                    'buffer_id',
                    ('msg', base64_encode),
                    ('flow_table', base64_encode_flow_table),
                    ('flow_mod', base64_encode),
                    ('removed', base64_encode),
                    ('expired_flows', base64_encode_flow_list),
                    ('matched_flow', base64_encode),
                    ('touched_flow', base64_encode),
                    'touched_flow_bytes',
                    ('touched_flow_now', lambda fp: repr(fp)), # str() is not precise for floating point numbers in Python < v3.2
                    ]

  _from_json_attrs = {
    'eid': lambda x: x,
    'dpid': lambda x: x,
    'controller_id': lambda x: x, # socket.getpeername(), NOT the STS cid
    'hid': lambda x: x,
    'packet': decode_packet,
    'in_port': lambda x: x,
    'out_port': lambda x: x,
    'buffer_id': lambda x: x,
    'msg': base64_decode_openflow,
    'flow_table': decode_flow_table,
    'flow_mod': decode_flow_mod,
    'removed': decode_flow_mod,
    'expired_flows': lambda flows: [decode_flow_mod(x) for x in flows],
    'matched_flow': decode_flow_mod,
    'touched_flow': decode_flow_mod,
    'touched_flow_bytes': lambda x: x,
    'touched_flow_now': lambda x: x,
  }

  def __init__(self, eid=None):
    super(TraceSwitchEvent, self).__init__(eid=eid)


class TraceAsyncSwitchFlowExpirationBegin(TraceSwitchEvent):
  def __init__(self, dpid, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid


class TraceAsyncSwitchFlowExpirationEnd(TraceSwitchEvent):
  def __init__(self, dpid, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid


class TraceSwitchPacketHandleBegin(TraceSwitchEvent):
  def __init__(self, dpid, packet, in_port, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet
    self.in_port = in_port


class TraceSwitchPacketHandleEnd(TraceSwitchEvent):
  def __init__(self, dpid, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid


# TODO(jm): remove this, and all uses of TraceSwitchMessageRx
class TraceSwitchMessageRx(TraceSwitchEvent):
  def __init__(self, dpid, controller_id, msg, b64msg, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.controller_id = controller_id
    self.msg = msg
    self.b64msg = b64msg


class TraceSwitchMessageTx(TraceSwitchEvent):
  def __init__(self, dpid, controller_id, msg, b64msg, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.controller_id = controller_id
    self.msg = msg
    self.b64msg = b64msg


class TraceSwitchMessageHandleBegin(TraceSwitchEvent):
  def __init__(self, dpid, controller_id, msg, msg_type, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.controller_id = controller_id
    self.msg = msg
    self.msg_type = msg_type

  @property
  def msg_type_str(self):
    return ofp_type_to_str(self.msg_type)



class TraceSwitchMessageHandleEnd(TraceSwitchEvent):
  def __init__(self, dpid, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
  
class TraceSwitchMessageSend(TraceSwitchEvent):
  def __init__(self, dpid, cid, controller_id, msg, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.cid = cid
    self.controller_id = controller_id
    self.msg = msg


class TraceSwitchPacketSend(TraceSwitchEvent):
  def __init__(self, dpid, packet, out_port, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet
    self.out_port = out_port


class TraceSwitchFlowTableRead(TraceSwitchEvent):
  def __init__(self, dpid, packet, in_port, flow_table, flow_mod,
               touched_flow_bytes=None, touched_flow_now=None, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet
    self.in_port = in_port
    self.flow_table = decode_flow_table(base64_encode_flow_table(flow_table))
    self.flow_mod = decode_flow_mod(base64_encode_flow(flow_mod))
    self.entry = decode_flow_mod(base64_encode_flow(flow_mod))
    self.touched_flow_bytes = touched_flow_bytes
    self.touched_flow_now = touched_flow_now


class TraceSwitchFlowTableWrite(TraceSwitchEvent):
  def __init__(self, dpid, flow_table, flow_mod, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.flow_table = decode_flow_table(base64_encode_flow_table(flow_table))
    self.flow_mod = decode_flow_mod(base64_encode_flow(flow_mod))
    
class TraceSwitchFlowTableEntryExpiry(TraceSwitchEvent):
  def __init__(self, dpid, flow_table, removed, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.flow_table = decode_flow_table(base64_encode_flow_table(flow_table))
    self.flow_mod = decode_flow_mod(base64_encode_flow(removed))
    self.removed = decode_flow_mod(base64_encode_flow(removed))


class TraceSwitchBufferPut(TraceSwitchEvent):
  def __init__(self, dpid, packet, in_port, buffer_id, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet
    self.in_port = in_port
    self.buffer_id = buffer_id


class TraceSwitchBufferGet(TraceSwitchEvent):
  def __init__(self, dpid, packet, in_port, buffer_id, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet
    self.in_port = in_port
    self.buffer_id = buffer_id

class TraceSwitchPacketUpdateBegin(TraceSwitchEvent):
  def __init__(self, dpid, packet, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet


class TraceSwitchPacketUpdateEnd(TraceSwitchEvent):
  def __init__(self, dpid, packet, eid=None):
    TraceSwitchEvent.__init__(self, eid=eid)
    self.dpid = dpid
    self.packet = packet


class TraceHostEvent(JsonEvent):
  def __init__(self, eid=None):
    super(TraceHostEvent, self).__init__(eid=eid)


class TraceHostPacketHandleBegin(TraceHostEvent):
  def __init__(self, hid, packet, in_port, eid=None):
    TraceHostEvent.__init__(self, eid=eid)
    self.hid = hid
    self.packet = packet
    self.in_port = in_port


class TraceHostPacketHandleEnd(TraceHostEvent):
  def __init__(self, hid, eid=None):
    TraceHostEvent.__init__(self, eid=eid)
    self.hid = hid


class TraceHostPacketSend(TraceHostEvent):
  def __init__(self, hid, packet, out_port, eid=None):
    TraceHostEvent.__init__(self, eid=eid)
    self.hid = hid
    self.packet = packet
    self.out_port = out_port
    

JsonEvent.register_type(TraceAsyncSwitchFlowExpirationBegin)
JsonEvent.register_type(TraceAsyncSwitchFlowExpirationEnd)
JsonEvent.register_type(TraceSwitchPacketHandleBegin)
JsonEvent.register_type(TraceSwitchPacketHandleEnd)
JsonEvent.register_type(TraceSwitchMessageRx)
JsonEvent.register_type(TraceSwitchMessageTx)
JsonEvent.register_type(TraceSwitchMessageHandleBegin)
JsonEvent.register_type(TraceSwitchMessageHandleEnd)
JsonEvent.register_type(TraceSwitchMessageSend)
JsonEvent.register_type(TraceSwitchPacketSend)
JsonEvent.register_type(TraceSwitchFlowTableRead)
JsonEvent.register_type(TraceSwitchFlowTableWrite)
JsonEvent.register_type(TraceSwitchFlowTableEntryExpiry)
JsonEvent.register_type(TraceSwitchBufferPut)
JsonEvent.register_type(TraceSwitchBufferGet)
JsonEvent.register_type(TraceSwitchPacketUpdateBegin)
JsonEvent.register_type(TraceSwitchPacketUpdateEnd)
JsonEvent.register_type(TraceHostEvent)
JsonEvent.register_type(TraceHostPacketHandleBegin)
JsonEvent.register_type(TraceHostPacketHandleEnd)
JsonEvent.register_type(TraceHostPacketSend)
