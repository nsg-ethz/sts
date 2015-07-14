from config.experiment_config_lib import ControllerConfig
from sts.topology import StarTopology, BufferedPatchPanel, MeshTopology, GridTopology, BinaryLeafTreeTopology
from sts.controller_manager import UserSpaceControllerPatchPanel
from sts.control_flow.fuzzer import Fuzzer
from sts.control_flow.interactive import Interactive
from sts.input_traces.input_logger import InputLogger
from sts.simulation_state import SimulationConfig
from sts.happensbefore.hb_logger import HappensBeforeLogger 
from config.application_events import AppCircuitPusher


start_cmd = ('''java -ea -Dlogback.configurationFile=./src/main/resources/logback-test-trace.xml -jar '''
             '''./target/floodlight.jar '''
              '''-cf ./src/main/resources/nohb_default.properties''')

# Uncomment this if you are running Floodlight separately, e.g. for debugging in Eclipse. There must be a controller listening on port 6633.
# start_cmd = '''echo "no-op"'''

controllers = [ControllerConfig(start_cmd, cwd='../floodlight', address="127.0.0.1", port=6633)]

topology_class = BinaryLeafTreeTopology
topology_params = "num_levels=1"
# topology_class = StarTopology
# topology_params = "num_hosts=3"

# Where should the output files be written to
results_dir = "experiments/floodlight_nohb_default"

apps = [AppCircuitPusher('circuitpusher', cwd='../floodlight/apps/circuitpusher', runtime='python', script='circuitpusher.py', controller='localhost:8080')]

# include all defaults
simulation_config = SimulationConfig(controller_configs=controllers,
                                     topology_class=topology_class,
                                     topology_params=topology_params,
                                     patch_panel_class=BufferedPatchPanel,
                                     controller_patch_panel_class=UserSpaceControllerPatchPanel,
                                     dataplane_trace=None,
                                     snapshot_service=None,
                                     multiplex_sockets=False,
                                     violation_persistence_threshold=None,
                                     kill_controllers_on_exit=True,
                                     interpose_on_controllers=False,
                                     ignore_interposition=False,
                                     hb_logger_class=HappensBeforeLogger,
                                     hb_logger_params=results_dir,
                                     apps=apps)

# Manual, interactive mode
# control_flow = Interactive(simulation_config, input_logger=InputLogger())

control_flow = Fuzzer(simulation_config,
                      input_logger=InputLogger(),
                      initialization_rounds=15,
#                       send_all_to_all=True,
                      check_interval=1000,
                      delay=0.1,
                      halt_on_violation=False,
                      invariant_check_name="InvariantChecker.check_liveness",
                      apps=apps)