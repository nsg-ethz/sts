

import os
import time
import logging
import networkx as nx

import utils

# create logger
logger = logging.getLogger(__name__)

def preprocess_hb_graph(hb_graph):
  """
  Preprocesses the whole hb_graph. Can be used to
  Args:
    hb_graph:

  Returns:

  """

def get_subgraphs(hb_graph, resultdir):
  """
  Removes unnecessary edge of the graph (based on time) and get a subgraph for each race.
  Each subgraph contains all the nodes starting from a HostSend event to an event that was part of a race.

  Args:
    hb_graph: hb graph
    resultdir: path to the result directory

  Returns: List of subgraphs
  """
  logger.debug("Get_subgraphs...")
  tstart = time.time()

  # Use a copy of the graph
  cg = hb_graph.g.copy()

  # Remove unnecessary edges
  for src, dst, data in cg.edges(data=True):
    if data.get('rel', None) in ['time', 'dep_raw']:
      cg.remove_edge(src, dst)

  # First start getting the simple paths between all host_sends and
  # the reachable race events
  harmful = hb_graph.race_detector.races_harmful

  # Generate list with all race ids, prepare dictionary for all paths to a race event
  all_paths = {}
  race_ids = []
  for race in harmful:
    i = race.i_event.eid
    k = race.k_event.eid
    race_ids.append(i)
    race_ids.append(k)
    if i not in all_paths:
      all_paths[i] = []
    if k not in all_paths:
      all_paths[k] = []

  # convert to set for faster lookup
  race_ids = set(race_ids)

  # find paths
  for ind, send in enumerate(hb_graph.host_sends):
    paths_to_race = get_path_to_race(cg, send, race_ids)
    for paths in paths_to_race:
      # last element is the race event id
      all_paths[paths[-1]].append(paths)

  # Now construct the subgraphs
  subgraphs = []
  for ind, race in enumerate(harmful):
    i = race.i_event.eid
    k = race.k_event.eid
    nodes = [i, k]
    for path in all_paths[i]:
      nodes.extend(path)
    for path in all_paths[k]:
      nodes.extend(path)

    nodes = list(set(nodes))

    subg = nx.DiGraph(cg.subgraph(nodes), race=race, index=ind)
    assert subg.graph['race'].i_event.eid in subg.nodes(), "x doesn't have i"
    assert subg.graph['race'].k_event.eid in subg.nodes(), "x doesn't have k"
    subgraphs.append(subg)
    subg.add_edge(i, k, rel='race', harmful=True)
    subg.edge[i][k]['color'] = 'red'
    subg.edge[i][k]['style'] = 'bold'

    # Export subgraphs
    export_path = os.path.join(resultdir, "subg_%03d.dot" % ind)
    nx.write_dot(subg, export_path)

  export_path = os.path.join(resultdir, "subg_all.dot")
  nx.write_dot(nx.disjoint_union_all(subgraphs), export_path)

  tsubgraphs = time.time()

  logger.debug("Timing Subgraphs: %f s" % (tsubgraphs - tstart))

  return subgraphs


def get_path_to_race(graph, host_send, race_ids):
  """
  Traverses the graph starting from host_send. Return paths from host_send to all reachable race events.
  Args:
    graph:      graph
    host_send:  host send event id
    races:      list of all race events

  Returns: path to all reachable race events
  """
  logger.debug("Find paths from host_send %s to all race events." % host_send)
  path = []             # Path to the current node
  visited = []          # List of all visited nodes
  paths_to_race = []    # All paths leading to a race event (list of paths)
  alt_paths = []        # List of alternative paths to a node
  _get_path_to_race(graph, host_send, race_ids, path, paths_to_race, visited, alt_paths)

  # check if we have alternative paths which lead to a race
  logger.debug("Number of paths to race events: %s" % len(paths_to_race))
  logger.debug("Number of alt_paths %s" % len(alt_paths))

  # first extend all alternative paths
  """
  for path in alt_paths[:]:
    for alt in alt_paths[:]:
      if alt == path:
        continue

      if alt[-1] in path:
        node_index = path.index(alt[-1])
        if len(path) > node_index + 1:
          alt_paths.append(alt + path[(node_index + 1):])
        else:
          alt_paths.append(alt)
  logger.debug("Number of extended alt_paths %s" % len(alt_paths))
  """

  # Construct all pathes to the race event with the alternative pathes until no new ones are found.
  old_len = 0
  while len(paths_to_race) > old_len:
    old_len = len(paths_to_race)
    for path in paths_to_race[:]:
      for alt in alt_paths[:]:
        if alt[-1] in path:
          node_index = path.index(alt[-1])
          if len(path) > node_index + 1:
            paths_to_race.append(alt + path[(node_index + 1):])
            alt_paths.remove(alt)

  logger.debug("Final number of paths to race events %s" % len(paths_to_race))

  return paths_to_race


def _get_path_to_race(graph, node, race_ids, path, paths_to_race, visited, alt_paths):
  """
  Recursive part of get_path_to_race.
  """
  path.append(node)

  # If node is already in path we can return, happens when there are multiple paths to a node
  if node in visited:
    # logger.debug("Node %d already visited." % node)
    alt_paths.append(path)
    return

  else:
    visited.append(node)

  # get children of node
  for child in graph.neighbors(node):
    _get_path_to_race(graph, child, race_ids, path[:], paths_to_race, visited, alt_paths)

  if node in race_ids:
    paths_to_race.append(path)

  return



