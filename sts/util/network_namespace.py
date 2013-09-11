# Copyright 2011-2013 Colin Scott
# Copyright 2012-2013 Sam Whitlock
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

'''
Utility functions for launching network namespaces.
'''

from pox.lib.addresses import EthAddr, IPAddr

import subprocess
import struct
import fcntl
import socket
from os import geteuid
from exceptions import EnvironmentError
from platform import system

ETH_P_ALL = 3                     # from linux/if_ether.h

def launch_namespace(cmd, ip_addr_str, iface_number):
  '''
  Set up and launch cmd in a new network namespace.

  Returns a tuple:
   (raw socket bound to host veth interface,
    Popen object for communicating with guest namespace,
    EthAddr object of guest's ethernet address,
    name of guest's veth interace)

  This method uses functionality that requires CAP_NET_ADMIN capabilites. This
  means that the calling method should check that the python process was
  launched as admin/superuser.

  Parameters:
    - cmd: the string to launch, in a separate namespace
    - ip_addr_str: the ip address to assign to the namespace's interace.
                   Must be a string! not a IPAddr object
    - iface_number: unique integer for the namespace and host virtual interfaces.
  '''
  if system() != 'Linux':
    raise EnvironmentError('network namespace functionality requires a Linux environment')

  uid = geteuid()
  if uid != 0:
    # user must have CAP_NET_ADMIN, which doesn't have to be su, but most often is
    raise EnvironmentError("superuser privileges required to launch network namespace")

  host_device = "heth%d" % (iface_number)
  guest_device = "geth%d" % (iface_number)

  try:
    null = open(os.devnull, 'wb') # FIXME(sw): this file is never actually closed

    # Clean up previos network namespaces
    # (Delete the device if it already exists)
    for dev in (host_device, guest_device):
      if subprocess.call(['ip', 'link', 'show', dev], stdout=null, stderr=null) == 0:
        subprocess.check_call(['ip', 'link', 'del', dev])

    # create a veth pair and set the host end to be promiscuous
    subprocess.check_call(['ip','link','add','name',host_device,'type','veth','peer','name',guest_device])
    subprocess.check_call(['ip','link','set',host_device,'promisc','on'])
    # Our end of the veth pair
    subprocess.check_call(['ip','link','set',host_device,'up'])
  except subprocess.CalledProcessError:
    raise # TODO raise a more informative exception

  guest_eth_addr = get_eth_address_for_interface(guest_device)

  # make the host-side (STS-side) socket
  # do this before unshare/fork to make failure/cleanup easier
  # Make sure we aren't monkeypatched first:
  if hasattr(socket, "_old_socket"):
    raise RuntimeError("MonkeyPatched socket! Bailing")
  s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, ETH_P_ALL)
  # Make sure the buffers are big enough to fit at least one full ethernet
  # packet
  s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
  s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
  s.bind((host_device, ETH_P_ALL))
  s.setblocking(0) # set non-blocking

  # all else should have succeeded, so now we fork and unshare for the guest
  # `ifconfig $ifname set ip $ifaddr netmask 255.255.255.0 up ; xterm`
  guest = subprocess.Popen(["unshare", "-n", "--", "/bin/bash"],
                           stdin=subprocess.PIPE)

  # push down the guest device into the netns
  try:
    subprocess.check_call(['ip', 'link', 'set', guest_device, 'netns', str(guest.pid)])
  except subprocess.CalledProcessError:
    # Failed to push down guest side of veth pair
    s.close()
    raise # TODO raise a more informative exception

  # Set the IP address of the virtual interface
  # TODO(cs): currently failing with the following error:
  #   set: Host name lookup failure
  #   ifconfig: `--help' gives usage information.
  # I think we may need to add an entry to /etc/hosts before invoking
  # ifconfig
  # For now, just force the user to configure it themselves in the xterm
  #guest.communicate("ifconfig %s set ip %s netmask 255.255.255.0 up" %
  #                  (guest_device,ip_addr_str))
  # Send the command
  guest.communicate(cmd)
  return (s, guest, guest_eth_addr, guest_device)

def get_eth_address_for_interface(ifname):
  '''Returns an EthAddr object from the interface specified by the argument.

  interface is a string, commonly eth0, wlan0, lo.'''
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  info = fcntl.ioctl(s.fileno(), 0x8927,  struct.pack('256s', ifname[:15]))
  return EthAddr(''.join(['%02x:' % ord(char) for char in info[18:24]])[:-1])

