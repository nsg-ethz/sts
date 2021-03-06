  # Copyright 2011-2013 Colin Scott
# Copyright 2011-2013 Andreas Wundsam
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import threading
import os
import signal
import sys
import time
import traceback
import shlex
from functools import partial
from pox.lib.revent import Event, EventMixin

from sts.util.console import color

def split_up(f, l):
  trues = []
  falses = []
  for elem in l:
    if(f(elem)):
      trues.append(elem)
    else:
      falses.append(elem)
  return (trues, falses)

def kill_procs(child_processes, kill=None, verbose=True, timeout=5,
               close_fds=True):
  child_processes = filter(lambda e: e is not None, child_processes)
  def msg(msg):
    if(verbose):
      sys.stderr.write(msg)

  if kill == None:
    if hasattr(kill_procs,"already_run"):
      kill = True
    else:
      kill = False
      kill_procs.already_run = True

  if len(child_processes) == 0:
    return

  msg("%s child controllers..." % ("Killing" if kill else "Terminating"))
  for child in child_processes:
    sig = signal.SIGKILL if kill else signal.SIGTERM
    pgid = os.getpgid(child.pid)
    if pgid == child.pid:
      # if the child is the leader in its process group (happens because of
      # the setsid in popen_filtered below), kill the entire process group.
      # this will take care of spawned children, e.g., the bash process
      # forked when shell=True
      os.killpg(pgid, sig)
    else:
      os.kill(child.pid, sig)

  start_time = time.time()
  last_dot = start_time
  all_dead = []
  while True:
    (child_processes, new_dead) = split_up(lambda child: child.poll() is None, child_processes)
    all_dead += new_dead
    if len(child_processes) == 0:
      break
    if hasattr(time, "_orig_sleep"):
      time._orig_sleep(0.1)
    else:
      time.sleep(0.1)
    now = time.time()
    if (now - last_dot) > 1:
      msg(".")
      last_dot = now
    if (now - start_time) > timeout:
      if kill:
        break
      else:
        msg(' FAILED (timeout)!\n')
        kill_procs(child_processes, kill=True)
        break

  if close_fds:
    for child in all_dead:
      for attr_name in "stdin", "stdout", "stderr":
        if hasattr(child, attr_name):
          try:
            attr = getattr(child, attr_name)
            if attr:
              attr.close()
          except IOError:
            msg("close() called on %s during concurrent operation\n" % attr_name)
          except:
            msg("Error closing child io.\n")
            tb = traceback.format_exc()
            msg(tb)

  if len(child_processes) == 0:
    msg(' OK\n')

class PrefixThreadLineMatch(Event):
  def __init__ (self, line=None, match=None):
    Event.__init__(self)
    self.line = line
    self.match = match

class PrefixThreadLineMatcher(EventMixin):
  _eventMixin_events = set([PrefixThreadLineMatch])
  
  def __init__(self):
    self.string_matches = []
    
  def add_string_to_match(self, match):
    self.string_matches.append(match)
    
  def match_line(self, line):
    for m in self.string_matches:
      if m in line:
        self.raiseEvent(PrefixThreadLineMatch, line=line, match=m)
    
    
prefixThreadOutputMatcher = PrefixThreadLineMatcher()

printlock = threading.Lock()
def _prefix_thread(f, func):
  def run():
    while not f.closed:
      line = f.readline()
      prefixThreadOutputMatcher.match_line(line)
      if not line:
        break
#       with printlock:
#         print func(line)
      # NOTE(jm): The printlock caused hangs with ONOS. This seems to "work". 
      #           see: https://stackoverflow.com/questions/3029816/how-do-i-get-a-thread-safe-print-in-python-2-6
      sys.stdout.write(str(func(line))+'\n')
    try:
      sys.stderr.write("Closing fd %d\n" % f)
      f.close() # idempotent, in case the f.closed broke out of the while loop
    except:
      # well, we tried
      pass
  t = threading.Thread(target=run)
  t.daemon = True
  t.start()
  return t



def color_normal(out_str, label):
  """Return normal colored text, see _prefix_thread"""
  return "%s%s %s%s\n" % (color.YELLOW, label, out_str.rstrip(), color.NORMAL)


def color_error(out_str, label):
  """Return error colored text, see _prefix_thread"""
  return "%s%s %s%s\n" % (color.B_RED + color.YELLOW, label, out_str.rstrip(),
                          color.NORMAL)


def popen_filtered(name, args, cwd=None, env=None, redirect_output=True,
                   shell=False):
  if shell and type(args) == list:
    args = ' '.join(args)
  try:
    # note: the preexec_fn below makes the process its own session leader.
    # This means that a CTRL-C on the shell will not be passed on to the process,
    # which enables us to jump between Fuzzing/Replay and Interactive mode.
    cmd = subprocess.Popen(args, stdout=subprocess.PIPE, shell=shell,
                           stderr=subprocess.PIPE, stdin=sys.stdin, cwd=cwd, env=env,
                           preexec_fn=lambda: os.setsid())
  except OSError as e:
    raise OSError("Error launching %s in directory %s: %s (error %d)" % (args, cwd, e.strerror, e.errno))
  if redirect_output:
    cmd._stdout_thread = _prefix_thread(cmd.stdout, partial(color_normal, label=name))
    cmd._stderr_thread = _prefix_thread(cmd.stderr, partial(color_error, label=name))
  return cmd

class PopenTerminationEvent(Event):
  def __init__ (self, cmd_id, cmd, return_code, return_out, return_err):
    Event.__init__(self)
    self.cmd_id = cmd_id
    self.cmd = cmd # Popen instance
    self.return_code = return_code # the return code
    self.return_out = return_out # the returned output from stdout
    self.return_err = return_err # the returned output from stderr
    
class PopenTerminationPublisher(EventMixin):
  _eventMixin_events = set([PopenTerminationEvent])
  
  def __init__(self):
    pass
    
  def publish(self, event):
    self.raiseEvent(event)

popenTerminationPublisher = PopenTerminationPublisher()

def _popen_background_exec_thread(cmd_id, cmd, piped_input=None):
  def run():
    if piped_input is not None:
      ret_out, ret_err = cmd.communicate(piped_input)
    else:
      ret_out, ret_err = cmd.communicate()
    ret_code = cmd.wait()
    event = PopenTerminationEvent(cmd_id, cmd, ret_code, ret_out, ret_err)
    popenTerminationPublisher.publish(event)

  t = threading.Thread(target=run)
  t.daemon = True
  t.start()
  return t

def popen_simple(args, cwd=None, env=None,
                     shell=False):
  ''' Execute an external command and return the Popen instance '''
  if shell and type(args) == list:
    args = ' '.join(args)
  try:
    cmd = subprocess.Popen(args, stdout=subprocess.PIPE, shell=shell,
                           stderr=subprocess.PIPE, stdin=subprocess.PIPE, cwd=cwd, env=env,
                           preexec_fn=lambda: os.setsid())      
  except OSError as e:
    raise OSError("Error launching %s in directory %s: %s (error %d)" % (args, cwd, e.strerror, e.errno))
  return cmd

def popen_background(cmd_id, args, cwd=None, env=None,
                     shell=False, piped_input=None):
  '''
  Execute an external command and raise a PopenTerminationEvent event once
  the command has terminated. The contents of piped_input will be piped
  into the stdin of the process.
  '''
  try:
    cmd = popen_simple(args,cwd,env,shell)
  except OSError as e:
    raise e
  cmd._background_exec_thread = _popen_background_exec_thread(cmd_id, cmd, piped_input)
  return cmd

def popen_blocking(cmd_id, args, cwd=None, env=None,
                     shell=False, piped_input=None):
  '''
  Execute an external command and wait until
  the command has terminated. The contents of piped_input will be piped
  into the stdin of the process. Returns a PopenTerminationEvent event.
  '''
  try:
    cmd = popen_simple(args,cwd,env,shell)
  except OSError as e:
    raise e
  if piped_input is not None:
    ret_out, ret_err = cmd.communicate(piped_input)
  else:
    ret_out, ret_err = cmd.communicate()
  ret_code = cmd.wait()
  event = PopenTerminationEvent(cmd_id, cmd, ret_code, ret_out, ret_err)
  return event

def cmdline_to_args(cmdline):
  ''' Safely convert a command line string to a list of arguments. '''
  args = shlex.split(cmdline);
  return args


