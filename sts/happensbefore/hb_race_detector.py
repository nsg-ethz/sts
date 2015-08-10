from collections import namedtuple
import itertools
import networkx as nx

from hb_utils import op_to_str
from hb_utils import nCr

from hb_comute_check import CommutativityChecker


# Sanity check! This is a mapping of all predecessor types that make sense.
predecessor_types = {
  'HbAsyncFlowExpiry': [
    'HbMessageSend',
  ],
  'HbPacketHandle': [
    'HbPacketSend',
    'HbHostSend',
  ],
  'HbPacketSend': [
    'HbPacketHandle',
    'HbMessageHandle',
  ],
  'HbMessageHandle': [
    'HbMessageHandle',
    'HbControllerSend',
    'HbPacketHandle', # buffer put -> buffer get!
    'HbMessageSend', # merged controller edges
  ],
  'HbMessageSend': [
    'HbAsyncFlowExpiry',
    'HbPacketHandle',
    'HbMessageHandle',
  ],
  'HbHostHandle': [
    'HbPacketSend'
  ],
  'HbHostSend': [
    'HbHostHandle',
  ],
  'HbControllerHandle': [
    'HbMessageSend',
  ],
  'HbControllerSend': [
    'HbControllerHandle',
  ],
}


# Define race type
Race = namedtuple('Race', ['rtype', 'i_event', 'i_op', 'k_event', 'k_op'])


class RaceDetector(object):

  # TODO(jm): make filter_rw a config option
  def __init__(self, graph, filter_rw=False):
    self.graph = graph

    self.read_operations = []
    self.write_operations = []
    self.races_harmful = []
    self.races_commute = []
    self.racing_events = set()
    self.racing_events_harmful = set()
    self.total_operations = 0
    self.total_harmful = 0
    self.total_commute = 0
    self.total_filtered = 0
    self.total_races = 0

    self.commutativity_checker = CommutativityChecker()

    self.filter_rw = filter_rw # Filter events with no common ancestor if True.

  def is_reachable(self, source, target):
    return nx.has_path(self.graph.g, source.eid, target.eid)

  def is_ordered(self, event, other):
    # TODO(jm): horribly inefficient, this should be done the right way
    older = event if event.eid < other.eid else other
    newer = event if event.eid > other.eid else other
    if self.is_reachable(newer, older):
      return True
    if self.is_reachable(older, newer): # need to check due to async controller instrumentation # TODO(jm): change numbering of controller events so that we can finally remove this
      return True
    return False

  def has_common_ancestor(self, event, other):
    """
    Returns true the two events have a common ancestor or they're ancestors of
    each other.
    """
    event_ancs = nx.ancestors(self.graph.g, event.eid)
    other_ancs = nx.ancestors(self.graph.g, other.eid)
    event_ancs.add(event.eid)
    other_ancs.add(other.eid)
    return not event_ancs.isdisjoint(other_ancs)

  def read_ops(self):
    """
    Helper method to extract read and write operations from the HB graph.
    MUST be called before detect_rw and detect_ww. But detect_races calls it
    automatically.
    """
    self.read_operations = []
    self.write_operations = []

    for i in self.graph.events:
      if hasattr(i, 'operations'):
        for k in i.operations:
          if k.type == 'TraceSwitchFlowTableWrite':
            assert hasattr(k, 'flow_table')
            assert hasattr(k, 'flow_mod')
            self.write_operations.append((i, k))
          elif k.type == 'TraceSwitchFlowTableRead':
            assert hasattr(k, 'flow_table')
            assert hasattr(k, 'flow_mod')
            assert hasattr(i, 'packet')
            assert hasattr(i, 'in_port')
            self.read_operations.append((i, k))

  def detect_ww_races(self, event=None, verbose=False):
    count = 0
    percentage_done = 0

    ww_combination_count = nCr(len(self.write_operations),2)

    if verbose:
      print "Processing {} w/w combinations".format(ww_combination_count)
    # write <-> write
    for (i_event, i_op), (k_event, k_op) in itertools.combinations(self.write_operations, 2):
      if verbose:
        count += 1
        percentage = int(((count / float(ww_combination_count)) * 100)) // 10 * 10
        if percentage > percentage_done:
          percentage_done = percentage
          print "{}% ".format(percentage)
      if (i_event != k_event and
          (event is None or event == i_event or event == k_event) and
          i_event.dpid == k_event.dpid and
          not self.is_ordered(i_event, k_event)):

        if self.commutativity_checker.check_commutativity_ww(i_event, i_op,
                                                             k_event, k_op):
          self.races_commute.append(Race('w/w', i_event, i_op, k_event, k_op))
        else:
          self.races_harmful.append(Race('w/w',i_event, i_op, k_event, k_op))
          self.racing_events_harmful.add(i_event)
          self.racing_events_harmful.add(k_event)
        self.racing_events.add(i_event)
        self.racing_events.add(k_event)

  def detect_rw_races(self, event=None, verbose=False):
    percentage_done = 0
    count = 0
    rw_combination_count = len(self.read_operations)*len(self.write_operations)

    if verbose:
      print "Processing {} r/w combinations".format(rw_combination_count)
    # read <-> write
    for i_event, i_op in self.read_operations:
      for k_event, k_op in self.write_operations:
        if verbose:
          count += 1
          percentage = int(((count / float(rw_combination_count)) * 100)) // 10 * 10
          if percentage > percentage_done:
            percentage_done = percentage
            print "{}% ".format(percentage)
        if (i_event != k_event and
            (event is None or event == i_event or event == k_event) and
            i_event.dpid == k_event.dpid and
            not self.is_ordered(i_event, k_event)):

          if self.filter_rw and not self.has_common_ancestor(i_event, k_event):
            self.total_filtered += 1
          else:
            if self.commutativity_checker.check_commutativity_rw(i_event, i_op,
                                                                 k_event, k_op):
              self.races_commute.append(Race('r/w',i_event, i_op, k_event, k_op))
            else:
              self.races_harmful.append(Race('r/w',i_event, i_op, k_event, k_op))
              self.racing_events_harmful.add(i_event)
              self.racing_events_harmful.add(k_event)
            self.racing_events.add(i_event)
            self.racing_events.add(k_event)

  # TODO(jm): make verbose a config option
  def detect_races(self, event=None, verbose=False):
    """
    Detect all races that involve event.
    Detect all races for all events if event is None.
    """
    self.read_ops()

    if verbose:
      print "Total write operations: {}".format(len(self.write_operations))
      print "Total read operations: {}".format(len(self.read_operations))

    self.races_harmful = []
    self.races_commute = []
    self.racing_events = set()
    self.racing_events_harmful = set()
    self.total_filtered = 0

    self.detect_ww_races(event, verbose)

    self.detect_rw_races(event, verbose)

    self.total_operations = len(self.write_operations) + len(self.read_operations)
    self.total_harmful = len(self.races_harmful)
    self.total_commute = len(self.races_commute)
    self.total_races = self.total_harmful + self.total_commute

  def print_races(self):
    for race in self.races_commute:
      print "+-------------------------------------------+"
      print "| Commuting ({}):     {:>4} <---> {:>4}      |".format(race.rtype, race.i_event.eid, race.k_event.eid)
      print "+-------------------------------------------+"
      print "| op # {:<8} t={:<26}|".format(race.i_op.eid, race.i_op.t)
      print "+-------------------------------------------+"
      print "| " + op_to_str(race.i_op)
      print "+-------------------------------------------+"
      print "| op # {:<8} t={:<26}|".format(race.k_op.eid, race.k_op.t)
      print "+-------------------------------------------+"
      print "| " + op_to_str(race.k_op)
      print "+-------------------------------------------+"
    for race in self.races_harmful:
      print "+-------------------------------------------+"
      print "| Harmful   ({}):     {:>4} >-!-< {:>4}      |".format(race.rtype, race.i_event.eid, race.k_event.eid)
      print "+-------------------------------------------+"
      print "| op # {:<8} t={:<26}|".format(race.i_op.eid, race.i_op.t)
      print "+-------------------------------------------+"
      print "| " + op_to_str(race.i_op)
      print "+-------------------------------------------+"
      print "| op # {:<8} t={:<26}|".format(race.k_op.eid, race.k_op.t)
      print "+-------------------------------------------+"
      print "| " + op_to_str(race.k_op)
      print "+-------------------------------------------+"
    print "+-------------------------------------------+"
    for ev in self.read_operations:
      print "| {:>4}: {:28} (read) |".format(ev[0].eid, ev[0].type)
    for ev in self.write_operations:
      print "| {:>4}: {:27} (write) |".format(ev[0].eid, ev[0].type)
    print "| Total operations:      {:<18} |".format(self.total_operations)
    print "|-------------------------------------------|"
    print "| Total commuting races: {:<18} |".format(self.total_commute)
    print "| Total harmful races:   {:<18} |".format(self.total_harmful)
    print "| Total filtered races:  {:<18} |".format(self.total_filtered)
    print "+-------------------------------------------+"
