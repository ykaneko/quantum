#!/usr/bin/env python
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright (C) 2014 Nippon Telegraph and Telephone Corporation.
# Copyright (C) 2014 VA Linux Systems Japan K.K.
# Based on openvswitch agent.
#
# Copyright 2011 Nicira Networks, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
# @author: Fumihiko Kakuma, VA Linux Systems Japan K.K.

import distutils.version as dist_version
import sys
import time

from oslo.config import cfg

from neutron.agent.linux import ip_lib
from neutron.agent.linux import ovs_lib
from neutron.agent.linux import polling
from neutron.agent.linux import utils
from neutron.agent import rpc as agent_rpc
from neutron.agent import securitygroups_rpc as sg_rpc
from neutron.common import constants as q_const
from neutron.common import topics
from neutron.common import utils as q_utils
from neutron import context
from neutron.extensions import securitygroup as ext_sg
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall
from neutron.openstack.common.rpc import common as rpc_common
from neutron.openstack.common.rpc import dispatcher
from neutron.plugins.common import constants as p_const
from neutron.plugins.ryu.common import config_ml2  # noqa
from neutron.plugins.ryu.common import constants

from ryu.app.ofctl import api as ryu_api
from ryu.base import app_manager
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3 as ryu_ofp13


LOG = logging.getLogger(__name__)
RYUAPP_INST = None

# A placeholder for dead vlans.
DEAD_VLAN_TAG = str(q_const.MAX_VLAN_TAG + 1)


# A class to represent a VIF (i.e., a port that has 'iface-id' and 'vif-mac'
# attributes set).
class LocalVLANMapping:
    def __init__(self, vlan, network_type, physical_network, segmentation_id,
                 vif_ports=None):
        if vif_ports is None:
            vif_ports = {}
        self.vlan = vlan
        self.network_type = network_type
        self.physical_network = physical_network
        self.segmentation_id = segmentation_id
        self.vif_ports = vif_ports
        # set of tunnel ports on which packets should be flooded
        self.tun_ofports = set()

    def __str__(self):
        return ("lv-id = %s type = %s phys-net = %s phys-id = %s" %
                (self.vlan, self.network_type, self.physical_network,
                 self.segmentation_id))


class Port(object):
    """Represents a neutron port.

    Class stores port data in a ORM-free way, so attributres are
    still available even if a row has been deleted.
    """

    def __init__(self, p):
        self.id = p.id
        self.network_id = p.network_id
        self.device_id = p.device_id
        self.admin_state_up = p.admin_state_up
        self.status = p.status

    def __eq__(self, other):
        '''Compare only fields that will cause us to re-wire.'''
        try:
            return (self and other
                    and self.id == other.id
                    and self.admin_state_up == other.admin_state_up)
        except Exception:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id)


class OVSBridge(ovs_lib.OVSBridge):
    def __init__(self, br_name, root_helper):
        super(OVSBridge, self).__init__(br_name, root_helper)
        self.datapath_id = None
        self.datapath = None
        self.ofparser = None

    def set_controller(self, controller_names):
        vsctl_command = ["--", "set-controller", self.br_name]
        vsctl_command.extend(controller_names)
        self.run_vsctl(vsctl_command, check_error=True)

    def del_controller(self):
        self.run_vsctl(["--", "del-controller", self.br_name])

    def get_controller(self):
        res = self.run_vsctl(["--", "get-controller", self.br_name])
        if res:
            return res.strip().split("\n")
        return []

    def set_protocols(self, protocols='OpenFlow13'):
        self.run_vsctl(['--', 'set', 'bridge', self.br_name,
                        "protocols=%s" % protocols],
                       check_error=True)

    def find_datapath_id(self):
        self.datapath_id = self.get_datapath_id()

    def get_datapath(self, retry_max=cfg.CONF.AGENT.get_datapath_retry_times):
        retry = 0
        while self.datapath is None:
            self.datapath = ryu_api.get_datapath(RYUAPP_INST,
                                                 int(self.datapath_id, 16))
            retry += 1
            if retry >= retry_max:
                LOG.error(_('Agent terminated!: Failed to get a datapath.'))
                sys.exit(1)
            time.sleep(1)
        self.ofparser = self.datapath.ofproto_parser

    def setup_ofp(self, controller_names=None,
                  protocols='OpenFlow13',
                  retry_max=cfg.CONF.AGENT.get_datapath_retry_times):
        if not controller_names:
            host = cfg.CONF.ofp_listen_host
            if not host:
                host = '127.0.0.1'
            controller_names = ["tcp:%s:%d" % (host,
                                               cfg.CONF.ofp_tcp_listen_port)]
        try:
            self.set_protocols(protocols)
            self.set_controller(controller_names)
        except Exception as e:
            LOG.error(_("Agent terminated: %s"), e)
            sys.exit(1)
        self.find_datapath_id()
        self.get_datapath(retry_max)


class RyuPluginApi(agent_rpc.PluginApi,
                   sg_rpc.SecurityGroupServerRpcApiMixin):
    pass


class RyuSecurityGroupAgent(sg_rpc.SecurityGroupAgentRpcMixin):
    def __init__(self, context, plugin_rpc, root_helper):
        self.context = context
        self.plugin_rpc = plugin_rpc
        self.root_helper = root_helper
        self.init_firewall()


class RyuNeutronAgentApp(app_manager.RyuApp):
    OFP_VERSIONS = [ryu_ofp13.OFP_VERSION]

    def start(self):
        global RYUAPP_INST

        super(RyuNeutronAgentApp, self).start()
        RYUAPP_INST = self
        return hub.spawn(self._agent_main)

    def _agent_main(self):
        cfg.CONF.register_opts(ip_lib.OPTS)

        try:
            agent_config = create_agent_config_map(cfg.CONF)
        except ValueError as e:
            LOG.error(_('%s Agent terminated!'), e)
            sys.exit(1)

        is_xen_compute_host = \
            'rootwrap-xen-dom0' in agent_config['root_helper']
        if is_xen_compute_host:
            # Force ip_lib to always use the root helper to ensure that ip
            # commands target xen dom0 rather than domU.
            cfg.CONF.set_default('ip_lib_force_root', True)

        agent = RyuNeutronAgent(**agent_config)

        # Start everything.
        LOG.info(_("Agent initialized successfully, now running... "))
        agent.daemon_loop()
        sys.exit(0)


class RyuNeutronAgent(sg_rpc.SecurityGroupAgentRpcCallbackMixin):
    '''Ryu agent for ML2.

    RyuNeutronAgent is a Ryu agent for a ML2 plugin.
    This is as a ryu application thread.
    '''

    # history
    #   1.0 Initial version
    #   1.1 Support Security Group RPC
    RPC_API_VERSION = '1.1'

    def __init__(self, integ_br, tun_br, local_ip,
                 bridge_mappings, root_helper,
                 polling_interval, tunnel_types=None,
                 veth_mtu=None, l2_population=False,
                 minimize_polling=False,
                 ovsdb_monitor_respawn_interval=(
                     constants.DEFAULT_RYUDBMON_RESPAWN)):
        '''Constructor.

        :param integ_br: name of the integration bridge.
        :param tun_br: name of the tunnel bridge.
        :param local_ip: local IP address of this hypervisor.
        :param bridge_mappings: mappings from physical network name to bridge.
        :param root_helper: utility to use when running shell cmds.
        :param polling_interval: interval (secs) to poll DB.
        :param tunnel_types: A list of tunnel types to enable support for in
               the agent. If set, will automatically set enable_tunneling to
               True.
        :param veth_mtu: MTU size for veth interfaces.
        :param minimize_polling: Optional, whether to minimize polling by
               monitoring ovsdb for interface changes.
        :param ovsdb_monitor_respawn_interval: Optional, when using polling
               minimization, the number of seconds to wait before respawning
               the ovsdb monitor.
        '''
        self.veth_mtu = veth_mtu
        self.root_helper = root_helper
        self.available_local_vlans = set(xrange(q_const.MIN_VLAN_TAG,
                                                q_const.MAX_VLAN_TAG))
        self.tunnel_types = tunnel_types or []
        self.l2_pop = l2_population
        self.agent_state = {
            'binary': 'neutron-ryu-ml2-agent',
            'host': cfg.CONF.host,
            'topic': q_const.L2_AGENT_TOPIC,
            'configurations': {'bridge_mappings': bridge_mappings,
                               'tunnel_types': self.tunnel_types,
                               'tunneling_ip': local_ip,
                               'l2_population': self.l2_pop},
            'agent_type': q_const.AGENT_TYPE_RYU,
            'start_flag': True}

        # Keep track of int_br's device count for use by _report_state()
        self.int_br_device_count = 0

        self.int_br = OVSBridge(integ_br, self.root_helper)
        self.setup_rpc()
        self.setup_integration_br()
        self.setup_physical_bridges(bridge_mappings)
        self.local_vlan_map = {}
        self.tun_br_ofports = {p_const.TYPE_GRE: {},
                               p_const.TYPE_VXLAN: {}}

        self.polling_interval = polling_interval
        self.minimize_polling = minimize_polling
        self.ovsdb_monitor_respawn_interval = ovsdb_monitor_respawn_interval

        if self.tunnel_types:
            self.enable_tunneling = True
        else:
            self.enable_tunneling = False
        self.local_ip = local_ip
        self.tunnel_count = 0
        self.vxlan_udp_port = cfg.CONF.AGENT.vxlan_udp_port
        self._check_ovs_version()
        if self.enable_tunneling:
            self.setup_tunnel_br(tun_br)
        # Collect additional bridges to monitor
        self.ancillary_brs = self.setup_ancillary_bridges(integ_br, tun_br)

        # Security group agent support
        self.sg_agent = RyuSecurityGroupAgent(self.context,
                                              self.plugin_rpc,
                                              self.root_helper)
        # Initialize iteration counter
        self.iter_num = 0

    def _check_ovs_version(self):
        if p_const.TYPE_VXLAN in self.tunnel_types:
            check_ovs_version(constants.MINIMUM_RYU_VXLAN_VERSION,
                              self.root_helper)

    def _report_state(self):
        # How many devices are likely used by a VM
        self.agent_state.get('configurations')['devices'] = (
            self.int_br_device_count)
        try:
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_("Failed reporting state!"))

    def ryu_send_msg(self, msg):
        result = ryu_api.send_msg(RYUAPP_INST, msg)
        LOG.info(_("ryu send_msg() result: %s"), result)

    def setup_rpc(self):
        mac = self.int_br.get_local_port_mac()
        self.agent_id = '%s%s' % ('ovs', (mac.replace(":", "")))
        self.topic = topics.AGENT
        self.plugin_rpc = RyuPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        # RPC network init
        self.context = context.get_admin_context_without_session()
        # Handle updates from service
        self.dispatcher = self.create_rpc_dispatcher()
        # Define the listening consumers for the agent
        consumers = [[topics.PORT, topics.UPDATE],
                     [topics.NETWORK, topics.DELETE],
                     [constants.TUNNEL, topics.UPDATE],
                     [topics.SECURITY_GROUP, topics.UPDATE]]
        self.connection = agent_rpc.create_consumers(self.dispatcher,
                                                     self.topic,
                                                     consumers)
        report_interval = cfg.CONF.AGENT.report_interval
        if report_interval:
            heartbeat = loopingcall.FixedIntervalLoopingCall(
                self._report_state)
            heartbeat.start(interval=report_interval)

    def get_net_uuid(self, vif_id):
        for network_id, vlan_mapping in self.local_vlan_map.iteritems():
            if vif_id in vlan_mapping.vif_ports:
                return network_id

    def network_delete(self, context, **kwargs):
        LOG.debug(_("network_delete received"))
        network_id = kwargs.get('network_id')
        LOG.debug(_("Delete %s"), network_id)
        # The network may not be defined on this agent
        lvm = self.local_vlan_map.get(network_id)
        if lvm:
            self.reclaim_local_vlan(network_id)
        else:
            LOG.debug(_("Network %s not used on agent."), network_id)

    def port_update(self, context, **kwargs):
        LOG.debug(_("port_update received"))
        port = kwargs.get('port')
        # Validate that port is on OVS
        vif_port = self.int_br.get_vif_port_by_id(port['id'])
        if not vif_port:
            return

        if ext_sg.SECURITYGROUPS in port:
            self.sg_agent.refresh_firewall()
        network_type = kwargs.get('network_type')
        segmentation_id = kwargs.get('segmentation_id')
        physical_network = kwargs.get('physical_network')
        self.treat_vif_port(vif_port, port['id'], port['network_id'],
                            network_type, physical_network,
                            segmentation_id, port['admin_state_up'])
        try:
            if port['admin_state_up']:
                # update plugin about port status
                self.plugin_rpc.update_device_up(self.context, port['id'],
                                                 self.agent_id,
                                                 cfg.CONF.host)
            else:
                # update plugin about port status
                self.plugin_rpc.update_device_down(self.context, port['id'],
                                                   self.agent_id,
                                                   cfg.CONF.host)
        except rpc_common.Timeout:
            LOG.error(_("RPC timeout while updating port %s"), port['id'])

    def tunnel_update(self, context, **kwargs):
        LOG.debug(_("tunnel_update received"))
        if not self.enable_tunneling:
            return
        tunnel_ip = kwargs.get('tunnel_ip')
        tunnel_id = kwargs.get('tunnel_id', tunnel_ip)
        if not tunnel_id:
            tunnel_id = tunnel_ip
        tunnel_type = kwargs.get('tunnel_type')
        if not tunnel_type:
            LOG.error(_("No tunnel_type specified, cannot create tunnels"))
            return
        if tunnel_type not in self.tunnel_types:
            LOG.error(_("tunnel_type %s not supported by agent"), tunnel_type)
            return
        if tunnel_ip == self.local_ip:
            return
        tun_name = '%s-%s' % (tunnel_type, tunnel_id)
        self.setup_tunnel_port(tun_name, tunnel_ip, tunnel_type)

    def create_rpc_dispatcher(self):
        '''Get the rpc dispatcher for this manager.

        If a manager would like to set an rpc API version, or support more than
        one class as the target of rpc messages, override this method.
        '''
        return dispatcher.RpcDispatcher([self])

    def provision_local_vlan(self, net_uuid, network_type, physical_network,
                             segmentation_id):
        '''Provisions a local VLAN.

        :param net_uuid: the uuid of the network associated with this vlan.
        :param network_type: the network type ('gre', 'vxlan', 'vlan', 'flat',
                                               'local')
        :param physical_network: the physical network for 'vlan' or 'flat'
        :param segmentation_id: the VID for 'vlan' or tunnel ID for 'tunnel'
        '''

        if not self.available_local_vlans:
            LOG.error(_("No local VLAN available for net-id=%s"), net_uuid)
            return
        lvid = self.available_local_vlans.pop()
        LOG.info(_("Assigning %(vlan_id)s as local vlan for "
                   "net-id=%(net_uuid)s"),
                 {'vlan_id': lvid, 'net_uuid': net_uuid})
        self.local_vlan_map[net_uuid] = LocalVLANMapping(lvid, network_type,
                                                         physical_network,
                                                         segmentation_id)

        if network_type in constants.TUNNEL_NETWORK_TYPES:
            if self.enable_tunneling:
                # outbound broadcast/multicast
                ofports = ','.join(self.tun_br_ofports[network_type].values())
                if ofports:
                    match = self.tun_br.ofparser.OFPMatch(
                        vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT)
                    actions = [
                        self.tun_br.ofparser.OFPActionPopVlan(),
                        self.tun_br.ofparser.OFPActionSetField(
                            tunnel_id=int(segmentation_id)),
                        self.tun_br.ofparser.OFPActionOutput(int(ofports), 0)]
                    instructions = [
                        self.tun_br.ofparser.OFPInstructionActions(
                            ryu_ofp13.OFPIT_APPLY_ACTIONS,
                            actions)]
                    msg = self.tun_br.ofparser.OFPFlowMod(
                        self.tun_br.datapath,
                        table_id=constants.FLOOD_TO_TUN,
                        priority=1,
                        match=match, instructions=instructions)
                    self.ryu_send_msg(msg)
                # inbound from tunnels: set lvid in the right table
                # and resubmit to Table LEARN_FROM_TUN for mac learning
                match = self.tun_br.ofparser.OFPMatch(
                    tunnel_id=int(segmentation_id))
                actions = [self.tun_br.ofparser.OFPActionSetField(
                    vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT)]
                instructions = [
                    self.tun_br.ofparser.OFPInstructionActions(
                        ryu_ofp13.OFPIT_APPLY_ACTIONS, actions),
                    self.tun_br.ofparser.OFPInstructionGotoTable(
                        table_id=constants.LEARN_FROM_TUN)]
                msg = self.tun_br.ofparser.OFPFlowMod(
                    self.tun_br.datapath,
                    table_id=constants.TUN_TABLE[network_type],
                    priority=1,
                    match=match,
                    instructions=instructions)
                self.ryu_send_msg(msg)
            else:
                LOG.error(_("Cannot provision %(network_type)s network for "
                          "net-id=%(net_uuid)s - tunneling disabled"),
                          {'network_type': network_type,
                           'net_uuid': net_uuid})
        elif network_type == p_const.TYPE_FLAT:
            if physical_network in self.phys_brs:
                # outbound
                br = self.phys_brs[physical_network]
                match = br.ofparser.OFPMatch(
                    in_port=int(self.phys_ofports[physical_network]),
                    vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT)
                actions = [
                    br.ofparser.OFPActionPopVlan(),
                    br.ofparser.OFPActionOutput(ryu_ofp13.OFPP_NORMAL, 0)]
                instructions = [br.ofparser.OFPInstructionActions(
                    ryu_ofp13.OFPIT_APPLY_ACTIONS, actions)]
                msg = br.ofparser.OFPFlowMod(br.datapath,
                                             priority=4,
                                             match=match,
                                             instructions=instructions)
                self.ryu_send_msg(msg)
                # inbound
                match = self.int_br.ofparser.OFPMatch(
                    in_port=int(self.int_ofports[physical_network]),
                    vlan_vid=0xffff)
                actions = [self.int_br.ofparser.OFPActionSetField(
                    vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT),
                    self.int_br.ofparser.OFPActionOutput(
                        ryu_ofp13.OFPP_NORMAL, 0)]
                instructions = [self.int_br.ofparser.OFPInstructionActions(
                    ryu_ofp13.OFPIT_APPLY_ACTIONS, actions)]
                msg = self.int_br.ofparser.OFPFlowMod(
                    self.int_br.datapath,
                    priority=3,
                    match=match,
                    instructions=instructions)
                self.ryu_send_msg(msg)
            else:
                LOG.error(_("Cannot provision flat network for "
                            "net-id=%(net_uuid)s - no bridge for "
                            "physical_network %(physical_network)s"),
                          {'net_uuid': net_uuid,
                           'physical_network': physical_network})
        elif network_type == p_const.TYPE_VLAN:
            if physical_network in self.phys_brs:
                # outbound
                br = self.phys_brs[physical_network]
                match = br.ofparser.OFPMatch(
                    in_port=int(self.phys_ofports[physical_network]),
                    vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT)
                actions = [br.ofparser.OFPActionSetField(
                    vlan_vid=int(segmentation_id) | ryu_ofp13.OFPVID_PRESENT),
                    br.ofparser.OFPActionOutput(ryu_ofp13.OFPP_NORMAL, 0)]
                instructions = [br.ofparser.OFPInstructionActions(
                    ryu_ofp13.OFPIT_APPLY_ACTIONS, actions)]
                msg = br.ofparser.OFPFlowMod(br.datapath,
                                             priority=4,
                                             match=match,
                                             instructions=instructions)
                self.ryu_send_msg(msg)
                # inbound
                match = self.int_br.ofparser.OFPMatch(
                    in_port=int(self.int_ofports[physical_network]),
                    vlan_vid=int(segmentation_id) | ryu_ofp13.OFPVID_PRESENT)
                actions = [self.int_br.ofparser.OFPActionSetField(
                    vlan_vid=int(lvid) | ryu_ofp13.OFPVID_PRESENT),
                    self.int_br.ofparser.OFPActionOutput(
                        ryu_ofp13.OFPP_NORMAL, 0)]
                instructions = [self.int_br.ofparser.OFPInstructionActions(
                    ryu_ofp13.OFPIT_APPLY_ACTIONS, actions)]
                msg = self.int_br.ofparser.OFPFlowMod(
                    self.int_br.datapath,
                    priority=3,
                    match=match,
                    instructions=instructions)
                self.ryu_send_msg(msg)
            else:
                LOG.error(_("Cannot provision VLAN network for "
                            "net-id=%(net_uuid)s - no bridge for "
                            "physical_network %(physical_network)s"),
                          {'net_uuid': net_uuid,
                           'physical_network': physical_network})
        elif network_type == p_const.TYPE_LOCAL:
            # no flows needed for local networks
            pass
        else:
            LOG.error(_("Cannot provision unknown network type "
                        "%(network_type)s for net-id=%(net_uuid)s"),
                      {'network_type': network_type,
                       'net_uuid': net_uuid})

    def reclaim_local_vlan(self, net_uuid):
        '''Reclaim a local VLAN.

        :param net_uuid: the network uuid associated with this vlan.
        :param lvm: a LocalVLANMapping object that tracks (vlan, lsw_id,
            vif_ids) mapping.
        '''
        lvm = self.local_vlan_map.pop(net_uuid, None)
        if lvm is None:
            LOG.debug(_("Network %s not used on agent."), net_uuid)
            return

        LOG.info(_("Reclaiming vlan = %(vlan_id)s from net-id = %(net_uuid)s"),
                 {'vlan_id': lvm.vlan,
                  'net_uuid': net_uuid})

        if lvm.network_type in constants.TUNNEL_NETWORK_TYPES:
            if self.enable_tunneling:
                match = self.tun_br.ofparser.OFPMatch(
                    tunnel_id=int(lvm.segmentation_id))
                msg = self.tun_br.ofparser.OFPFlowMod(
                    self.tun_br.datapath,
                    table_id=constants.TUN_TABLE[lvm.network_type],
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
                match = self.tun_br.ofparser.OFPMatch(
                    vlan_vid=int(lvm.vlan) | ryu_ofp13.OFPVID_PRESENT)
                msg = self.tun_br.ofparser.OFPFlowMod(
                    self.tun_br.datapath,
                    table_id=ryu_ofp13.OFPTT_ALL,
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
        elif lvm.network_type == p_const.TYPE_FLAT:
            if lvm.physical_network in self.phys_brs:
                # outbound
                br = self.phys_brs[lvm.physical_network]
                match = br.ofparser.OFPMatch(
                    in_port=self.phys_ofports[lvm.physical_network],
                    vlan_vid=int(lvm.vlan) | ryu_ofp13.OFPVID_PRESENT)
                msg = br.ofparser.OFPFlowMod(
                    br.datapath,
                    table_id=ryu_ofp13.OFPTT_ALL,
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
                # inbound
                br = self.int_br
                match = br.ofparser.OFPMatch(
                    in_port=self.int_ofports[lvm.physical_network],
                    vlan_vid=0xffff)
                msg = br.ofparser.OFPFlowMod(
                    br.datapath,
                    table_id=ryu_ofp13.OFPTT_ALL,
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
        elif lvm.network_type == p_const.TYPE_VLAN:
            if lvm.physical_network in self.phys_brs:
                # outbound
                br = self.phys_brs[lvm.physical_network]
                match = br.ofparser.OFPMatch(
                    in_port=self.phys_ofports[lvm.physical_network],
                    vlan_vid=int(lvm.vlan) | ryu_ofp13.OFPVID_PRESENT)
                msg = br.ofparser.OFPFlowMod(
                    br.datapath,
                    table_id=ryu_ofp13.OFPTT_ALL,
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
                # inbound
                br = self.int_br
                match = br.ofparser.OFPMatch(
                    in_port=self.int_ofports[lvm.physical_network],
                    vlan_vid=lvm.segmentation_id | ryu_ofp13.OFPVID_PRESENT)
                msg = br.ofparser.OFPFlowMod(
                    br.datapath,
                    table_id=ryu_ofp13.OFPTT_ALL,
                    command=ryu_ofp13.OFPFC_DELETE,
                    out_group=ryu_ofp13.OFPG_ANY,
                    out_port=ryu_ofp13.OFPP_ANY,
                    match=match)
                self.ryu_send_msg(msg)
        elif lvm.network_type == p_const.TYPE_LOCAL:
            # no flows needed for local networks
            pass
        else:
            LOG.error(_("Cannot reclaim unknown network type "
                        "%(network_type)s for net-id=%(net_uuid)s"),
                      {'network_type': lvm.network_type,
                       'net_uuid': net_uuid})

        self.available_local_vlans.add(lvm.vlan)

    def port_bound(self, port, net_uuid,
                   network_type, physical_network, segmentation_id):
        '''Bind port to net_uuid/lsw_id and install flow for inbound traffic
        to vm.

        :param port: a ovs_lib.VifPort object.
        :param net_uuid: the net_uuid this port is to be associated with.
        :param network_type: the network type ('gre', 'vlan', 'flat', 'local')
        :param physical_network: the physical network for 'vlan' or 'flat'
        :param segmentation_id: the VID for 'vlan' or tunnel ID for 'tunnel'
        '''
        if net_uuid not in self.local_vlan_map:
            self.provision_local_vlan(net_uuid, network_type,
                                      physical_network, segmentation_id)
        lvm = self.local_vlan_map[net_uuid]
        lvm.vif_ports[port.vif_id] = port

        self.int_br.set_db_attribute("Port", port.port_name, "tag",
                                     str(lvm.vlan))
        if int(port.ofport) != -1:
            match = self.int_br.ofparser.OFPMatch(in_port=port.ofport)
            msg = self.int_br.ofparser.OFPFlowMod(
                self.int_br.datapath,
                table_id=ryu_ofp13.OFPTT_ALL,
                command=ryu_ofp13.OFPFC_DELETE,
                out_group=ryu_ofp13.OFPG_ANY,
                out_port=ryu_ofp13.OFPP_ANY,
                match=match)
            self.ryu_send_msg(msg)

    def port_unbound(self, vif_id, net_uuid=None):
        '''Unbind port.

        Removes corresponding local vlan mapping object if this is its last
        VIF.

        :param vif_id: the id of the vif
        :param net_uuid: the net_uuid this port is associated with.
        '''
        if net_uuid is None:
            net_uuid = self.get_net_uuid(vif_id)

        if not self.local_vlan_map.get(net_uuid):
            LOG.info(_('port_unbound() net_uuid %s not in local_vlan_map'),
                     net_uuid)
            return

        lvm = self.local_vlan_map[net_uuid]
        lvm.vif_ports.pop(vif_id, None)

        if not lvm.vif_ports:
            self.reclaim_local_vlan(net_uuid)

    def port_dead(self, port):
        '''Once a port has no binding, put it on the "dead vlan".

        :param port: a ovs_lib.VifPort object.
        '''
        self.int_br.set_db_attribute("Port", port.port_name, "tag",
                                     DEAD_VLAN_TAG)
        match = self.tun_br.ofparser.OFPMatch(in_port=int(port.ofport))
        msg = self.int_br.ofparser.OFPFlowMod(self.int_br.datapath,
                                              priority=2, match=match)
        self.ryu_send_msg(msg)

    def setup_integration_br(self):
        '''Setup the integration bridge.

        Create patch ports and remove all existing flows.

        :param bridge_name: the name of the integration bridge.
        :returns: the integration bridge
        '''
        self.int_br.setup_ofp()
        self.int_br.delete_port(cfg.CONF.RYU.int_peer_patch_port)
        msg = self.int_br.ofparser.OFPFlowMod(self.int_br.datapath,
                                              table_id=ryu_ofp13.OFPTT_ALL,
                                              command=ryu_ofp13.OFPFC_DELETE,
                                              out_group=ryu_ofp13.OFPG_ANY,
                                              out_port=ryu_ofp13.OFPP_ANY)
        self.ryu_send_msg(msg)
        # switch all traffic using L2 learning
        actions = [self.int_br.ofparser.OFPActionOutput(
            ryu_ofp13.OFPP_NORMAL, 0)]
        instructions = [self.int_br.ofparser.OFPInstructionActions(
            ryu_ofp13.OFPIT_APPLY_ACTIONS,
            actions)]
        msg = self.int_br.ofparser.OFPFlowMod(self.int_br.datapath,
                                              priority=1,
                                              instructions=instructions)
        self.ryu_send_msg(msg)

    def setup_ancillary_bridges(self, integ_br, tun_br):
        '''Setup ancillary bridges - for example br-ex.'''
        ovs_bridges = set(ovs_lib.get_bridges(self.root_helper))
        # Remove all known bridges
        ovs_bridges.remove(integ_br)
        if self.enable_tunneling:
            ovs_bridges.remove(tun_br)
        br_names = [self.phys_brs[physical_network].br_name for
                    physical_network in self.phys_brs]
        ovs_bridges.difference_update(br_names)
        # Filter list of bridges to those that have external
        # bridge-id's configured
        br_names = []
        for bridge in ovs_bridges:
            id = ovs_lib.get_bridge_external_bridge_id(self.root_helper,
                                                       bridge)
            if id != bridge:
                br_names.append(bridge)
        ovs_bridges.difference_update(br_names)
        ancillary_bridges = []
        for bridge in ovs_bridges:
            br = OVSBridge(bridge, self.root_helper)
            LOG.info(_('Adding %s to list of bridges.'), bridge)
            ancillary_bridges.append(br)
        return ancillary_bridges

    def setup_tunnel_br(self, tun_br):
        '''Setup the tunnel bridge.

        Creates tunnel bridge, and links it to the integration bridge
        using a patch port.

        :param tun_br: the name of the tunnel bridge.
        '''
        self.tun_br = OVSBridge(tun_br, self.root_helper)
        self.tun_br.reset_bridge()
        self.tun_br.setup_ofp()
        self.patch_tun_ofport = self.int_br.add_patch_port(
            cfg.CONF.RYU.int_peer_patch_port, cfg.CONF.RYU.tun_peer_patch_port)
        self.patch_int_ofport = self.tun_br.add_patch_port(
            cfg.CONF.RYU.tun_peer_patch_port, cfg.CONF.RYU.int_peer_patch_port)
        if int(self.patch_tun_ofport) < 0 or int(self.patch_int_ofport) < 0:
            LOG.error(_("Failed to create OVS patch port. Cannot have "
                        "tunneling enabled on this agent, since this version "
                        "of OVS does not support tunnels or patch ports. "
                        "Agent terminated!"))
            sys.exit(1)
        msg = self.tun_br.ofparser.OFPFlowMod(self.tun_br.datapath,
                                              table_id=ryu_ofp13.OFPTT_ALL,
                                              command=ryu_ofp13.OFPFC_DELETE,
                                              out_group=ryu_ofp13.OFPG_ANY,
                                              out_port=ryu_ofp13.OFPP_ANY)
        self.ryu_send_msg(msg)

        # Table 0 (default) will sort incoming traffic depending on in_port
        match = self.tun_br.ofparser.OFPMatch(
            in_port=int(self.patch_int_ofport))
        instructions = [self.tun_br.ofparser.OFPInstructionGotoTable(
            table_id=constants.PATCH_LV_TO_TUN)]
        msg = self.tun_br.ofparser.OFPFlowMod(self.tun_br.datapath,
                                              priority=1,
                                              match=match,
                                              instructions=instructions)
        self.ryu_send_msg(msg)
        msg = self.tun_br.ofparser.OFPFlowMod(self.tun_br.datapath, priority=0)
        self.ryu_send_msg(msg)
        # PATCH_LV_TO_TUN table will handle packets coming from patch_int
        # unicasts go to table UCAST_TO_TUN where remote adresses are learnt
        match = self.tun_br.ofparser.OFPMatch(eth_dst=('00:00:00:00:00:00',
                                                       '01:00:00:00:00:00'))
        instructions = [self.tun_br.ofparser.OFPInstructionGotoTable(
            table_id=constants.UCAST_TO_TUN)]
        msg = self.tun_br.ofparser.OFPFlowMod(
            self.tun_br.datapath,
            table_id=constants.PATCH_LV_TO_TUN,
            match=match,
            instructions=instructions)
        self.ryu_send_msg(msg)
        # Broadcasts/multicasts go to table FLOOD_TO_TUN that handles flooding
        match = self.tun_br.ofparser.OFPMatch(eth_dst=('01:00:00:00:00:00',
                                                       '01:00:00:00:00:00'))
        instructions = [self.tun_br.ofparser.OFPInstructionGotoTable(
            table_id=constants.FLOOD_TO_TUN)]
        msg = self.tun_br.ofparser.OFPFlowMod(
            self.tun_br.datapath,
            table_id=constants.PATCH_LV_TO_TUN,
            match=match, instructions=instructions)
        self.ryu_send_msg(msg)
        # Tables [tunnel_type]_TUN_TO_LV will set lvid depending on tun_id
        # for each tunnel type, and resubmit to table LEARN_FROM_TUN where
        # remote mac adresses will be learnt
        for tunnel_type in constants.TUNNEL_NETWORK_TYPES:
            msg = self.tun_br.ofparser.OFPFlowMod(
                self.tun_br.datapath,
                table_id=constants.TUN_TABLE[tunnel_type],
                priority=0)
            self.ryu_send_msg(msg)
        # Packet is outputed to patch_int
        actions = [self.tun_br.ofparser.OFPActionOutput(
            int(self.patch_int_ofport), 0)]
        instructions = [self.tun_br.ofparser.OFPInstructionActions(
            ryu_ofp13.OFPIT_APPLY_ACTIONS,
            actions)]
        msg = self.tun_br.ofparser.OFPFlowMod(
            self.tun_br.datapath,
            table_id=constants.LEARN_FROM_TUN,
            priority=1,
            instructions=instructions)
        self.ryu_send_msg(msg)
        # Egress unicast will be handled in table UCAST_TO_TUN.
        # For now, just add a default flow that will go unknown unicasts
        # to table FLOOD_TO_TUN to treat them as broadcasts/multicasts
        instructions = [self.tun_br.ofparser.OFPInstructionGotoTable(
            table_id=constants.FLOOD_TO_TUN)]
        msg = self.tun_br.ofparser.OFPFlowMod(
            self.tun_br.datapath,
            table_id=constants.UCAST_TO_TUN,
            priority=0,
            instructions=instructions)
        self.ryu_send_msg(msg)
        # FLOOD_TO_TUN will handle flooding in tunnels based on lvid,
        # for now, add a default drop action
        msg = self.tun_br.ofparser.OFPFlowMod(
            self.tun_br.datapath,
            table_id=constants.FLOOD_TO_TUN,
            priority=0)
        self.ryu_send_msg(msg)

    def setup_physical_bridges(self, bridge_mappings):
        '''Setup the physical network bridges.

        Creates physical network bridges and links them to the
        integration bridge using veths.

        :param bridge_mappings: map physical network names to bridge names.
        '''
        self.phys_brs = {}
        self.int_ofports = {}
        self.phys_ofports = {}
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        for physical_network, bridge in bridge_mappings.iteritems():
            LOG.info(_("Mapping physical network %(physical_network)s to "
                       "bridge %(bridge)s"),
                     {'physical_network': physical_network,
                      'bridge': bridge})
            # setup physical bridge
            if not ip_lib.device_exists(bridge, self.root_helper):
                LOG.error(_("Bridge %(bridge)s for physical network "
                            "%(physical_network)s does not exist. Agent "
                            "terminated!"),
                          {'physical_network': physical_network,
                           'bridge': bridge})
                sys.exit(1)
            br = OVSBridge(bridge, self.root_helper)
            br.setup_ofp()
            msg = br.ofparser.OFPFlowMod(br.datapath,
                                         table_id=ryu_ofp13.OFPTT_ALL,
                                         command=ryu_ofp13.OFPFC_DELETE,
                                         out_group=ryu_ofp13.OFPG_ANY,
                                         out_port=ryu_ofp13.OFPP_ANY)
            self.ryu_send_msg(msg)
            actions = [br.ofparser.OFPActionOutput(ryu_ofp13.OFPP_NORMAL, 0)]
            instructions = [br.ofparser.OFPInstructionActions(
                ryu_ofp13.OFPIT_APPLY_ACTIONS,
                actions)]
            msg = br.ofparser.OFPFlowMod(br.datapath,
                                         priority=1,
                                         instructions=instructions)
            self.ryu_send_msg(msg)
            self.phys_brs[physical_network] = br

            # create veth to patch physical bridge with integration bridge
            int_veth_name = constants.VETH_INTEGRATION_PREFIX + bridge
            self.int_br.delete_port(int_veth_name)
            phys_veth_name = constants.VETH_PHYSICAL_PREFIX + bridge
            br.delete_port(phys_veth_name)
            if ip_lib.device_exists(int_veth_name, self.root_helper):
                ip_lib.IPDevice(int_veth_name, self.root_helper).link.delete()
                # Give udev a chance to process its rules here, to avoid
                # race conditions between commands launched by udev rules
                # and the subsequent call to ip_wrapper.add_veth
                utils.execute(['/sbin/udevadm', 'settle', '--timeout=10'])
            int_veth, phys_veth = ip_wrapper.add_veth(int_veth_name,
                                                      phys_veth_name)
            self.int_ofports[physical_network] = self.int_br.add_port(int_veth)
            self.phys_ofports[physical_network] = br.add_port(phys_veth)

            # block all untranslated traffic over veth between bridges
            match = br.ofparser.OFPMatch(in_port=int(
                self.int_ofports[physical_network]))
            msg = br.ofparser.OFPFlowMod(self.int_br.datapath,
                                         priority=2, match=match)
            self.ryu_send_msg(msg)
            match = br.ofparser.OFPMatch(in_port=int(
                self.phys_ofports[physical_network]))
            msg = br.ofparser.OFPFlowMod(br.datapath, priority=2, match=match)
            self.ryu_send_msg(msg)

            # enable veth to pass traffic
            int_veth.link.set_up()
            phys_veth.link.set_up()

            if self.veth_mtu:
                # set up mtu size for veth interfaces
                int_veth.link.set_mtu(self.veth_mtu)
                phys_veth.link.set_mtu(self.veth_mtu)

    def update_ports(self, registered_ports):
        ports = self.int_br.get_vif_port_set()
        if ports == registered_ports:
            return
        self.int_br_device_count = len(ports)
        added = ports - registered_ports
        removed = registered_ports - ports
        return {'current': ports,
                'added': added,
                'removed': removed}

    def update_ancillary_ports(self, registered_ports):
        ports = set()
        for bridge in self.ancillary_brs:
            ports |= bridge.get_vif_port_set()

        if ports == registered_ports:
            return
        added = ports - registered_ports
        removed = registered_ports - ports
        return {'current': ports,
                'added': added,
                'removed': removed}

    def treat_vif_port(self, vif_port, port_id, network_id, network_type,
                       physical_network, segmentation_id, admin_state_up):
        if vif_port:
            if admin_state_up:
                self.port_bound(vif_port, network_id, network_type,
                                physical_network, segmentation_id)
            else:
                self.port_dead(vif_port)
        else:
            LOG.debug(_("No VIF port for port %s defined on agent."), port_id)

    def setup_tunnel_port(self, port_name, remote_ip, tunnel_type):
        ofport = self.tun_br.add_tunnel_port(port_name,
                                             remote_ip,
                                             self.local_ip,
                                             tunnel_type,
                                             self.vxlan_udp_port)
        ofport_int = -1
        try:
            ofport_int = int(ofport)
        except (TypeError, ValueError):
            LOG.exception(_("ofport should have a value that can be "
                            "interpreted as an integer"))
        if ofport_int < 0:
            LOG.error(_("Failed to set-up %(type)s tunnel port to %(ip)s"),
                      {'type': tunnel_type, 'ip': remote_ip})
            return 0

        self.tun_br_ofports[tunnel_type][remote_ip] = ofport
        # Add flow in default table to resubmit to the right
        # tunelling table (lvid will be set in the latter)
        match = self.tun_br.ofparser.OFPMatch(in_port=int(ofport))
        instructions = [self.tun_br.ofparser.OFPInstructionGotoTable(
            table_id=constants.TUN_TABLE[tunnel_type])]
        msg = self.tun_br.ofparser.OFPFlowMod(self.tun_br.datapath,
                                              priority=1,
                                              match=match,
                                              instructions=instructions)
        self.ryu_send_msg(msg)

        ofports = ','.join(self.tun_br_ofports[tunnel_type].values())
        if ofports:
            # Update flooding flows to include the new tunnel
            for network_id, vlan_mapping in self.local_vlan_map.iteritems():
                if vlan_mapping.network_type == tunnel_type:
                    match = self.tun_br.ofparser.OFPMatch(
                        vlan_vid=int(vlan_mapping.vlan) |
                        ryu_ofp13.OFPVID_PRESENT)
                    actions = [
                        self.tun_br.ofparser.OFPActionPopVlan(),
                        self.tun_br.ofparser.OFPActionSetField(
                            tunnel_id=int(vlan_mapping.segmentation_id)),
                        self.tun_br.ofparser.OFPActionOutput(int(ofports), 0)]
                    instructions = [
                        self.tun_br.ofparser.OFPInstructionActions(
                            ryu_ofp13.OFPIT_APPLY_ACTIONS,
                            actions)]
                    msg = self.tun_br.ofparser.OFPFlowMod(
                        self.tun_br.datapath,
                        table_id=constants.FLOOD_TO_TUN,
                        priority=1,
                        match=match,
                        instructions=instructions)
                    self.ryu_send_msg(msg)
        return ofport

    def cleanup_tunnel_port(self, tun_ofport, tunnel_type):
        # Check if this tunnel port is still used
        for lvm in self.local_vlan_map.values():
            if tun_ofport in lvm.tun_ofports:
                break
        # If not, remove it
        else:
            for remote_ip, ofport in self.tun_br_ofports[tunnel_type].items():
                if ofport == tun_ofport:
                    port_name = '%s-%s' % (tunnel_type, remote_ip)
                    self.tun_br.delete_port(port_name)
                    self.tun_br_ofports[tunnel_type].pop(remote_ip, None)

    def treat_devices_added(self, devices):
        resync = False
        self.sg_agent.prepare_devices_filter(devices)
        for device in devices:
            LOG.info(_("Port %s added"), device)
            try:
                details = self.plugin_rpc.get_device_details(self.context,
                                                             device,
                                                             self.agent_id)
            except Exception as e:
                LOG.debug(_("Unable to get port details for "
                            "%(device)s: %(e)s"),
                          {'device': device, 'e': e})
                resync = True
                continue
            port = self.int_br.get_vif_port_by_id(details['device'])
            if 'port_id' in details:
                LOG.info(_("Port %(device)s updated. Details: %(details)s"),
                         {'device': device, 'details': details})
                self.treat_vif_port(port, details['port_id'],
                                    details['network_id'],
                                    details['network_type'],
                                    details['physical_network'],
                                    details['segmentation_id'],
                                    details['admin_state_up'])

                # update plugin about port status
                self.plugin_rpc.update_device_up(self.context,
                                                 device,
                                                 self.agent_id,
                                                 cfg.CONF.host)
            else:
                LOG.debug(_("Device %s not defined on plugin"), device)
                if (port and int(port.ofport) != -1):
                    self.port_dead(port)
        return resync

    def treat_ancillary_devices_added(self, devices):
        resync = False
        for device in devices:
            LOG.info(_("Ancillary Port %s added"), device)
            try:
                self.plugin_rpc.get_device_details(self.context, device,
                                                   self.agent_id)
            except Exception as e:
                LOG.debug(_("Unable to get port details for "
                            "%(device)s: %(e)s"),
                          {'device': device, 'e': e})
                resync = True
                continue

            # update plugin about port status
            self.plugin_rpc.update_device_up(self.context,
                                             device,
                                             self.agent_id,
                                             cfg.CONF.host)
        return resync

    def treat_devices_removed(self, devices):
        resync = False
        self.sg_agent.remove_devices_filter(devices)
        for device in devices:
            LOG.info(_("Attachment %s removed"), device)
            try:
                self.plugin_rpc.update_device_down(self.context,
                                                   device,
                                                   self.agent_id,
                                                   cfg.CONF.host)
            except Exception as e:
                LOG.debug(_("port_removed failed for %(device)s: %(e)s"),
                          {'device': device, 'e': e})
                resync = True
                continue
            self.port_unbound(device)
        return resync

    def treat_ancillary_devices_removed(self, devices):
        resync = False
        for device in devices:
            LOG.info(_("Attachment %s removed"), device)
            try:
                details = self.plugin_rpc.update_device_down(self.context,
                                                             device,
                                                             self.agent_id,
                                                             cfg.CONF.host)
            except Exception as e:
                LOG.debug(_("port_removed failed for %(device)s: %(e)s"),
                          {'device': device, 'e': e})
                resync = True
                continue
            if details['exists']:
                LOG.info(_("Port %s updated."), device)
                # Nothing to do regarding local networking
            else:
                LOG.debug(_("Device %s not defined on plugin"), device)
        return resync

    def process_network_ports(self, port_info):
        resync_a = False
        resync_b = False
        if 'added' in port_info:
            start = time.time()
            resync_a = self.treat_devices_added(port_info['added'])
            LOG.debug(_("process_network_ports - iteration:%(iter_num)d -"
                        "treat_devices_added completed in %(elapsed).3f"),
                      {'iter_num': self.iter_num,
                       'elapsed': time.time() - start})
        if 'removed' in port_info:
            start = time.time()
            resync_b = self.treat_devices_removed(port_info['removed'])
            LOG.debug(_("process_network_ports - iteration:%(iter_num)d -"
                        "treat_devices_removed completed in %(elapsed).3f"),
                      {'iter_num': self.iter_num,
                       'elapsed': time.time() - start})
        # If one of the above opertaions fails => resync with plugin
        return (resync_a | resync_b)

    def process_ancillary_network_ports(self, port_info):
        resync_a = False
        resync_b = False
        if 'added' in port_info:
            start = time.time()
            resync_a = self.treat_ancillary_devices_added(port_info['added'])
            LOG.debug(_("process_ancillary_network_ports - iteration: "
                        "%(iter_num)d - treat_ancillary_devices_added "
                        "completed in %(elapsed).3f"),
                      {'iter_num': self.iter_num,
                       'elapsed': time.time() - start})
        if 'removed' in port_info:
            start = time.time()
            resync_b = self.treat_ancillary_devices_removed(
                port_info['removed'])
            LOG.debug(_("process_ancillary_network_ports - iteration: "
                        "%(iter_num)d - treat_ancillary_devices_removed "
                        "completed in %(elapsed).3f"),
                      {'iter_num': self.iter_num,
                       'elapsed': time.time() - start})

        # If one of the above opertaions fails => resync with plugin
        return (resync_a | resync_b)

    def tunnel_sync(self):
        resync = False
        try:
            for tunnel_type in self.tunnel_types:
                details = self.plugin_rpc.tunnel_sync(self.context,
                                                      self.local_ip,
                                                      tunnel_type)
                tunnels = details['tunnels']
                for tunnel in tunnels:
                    if self.local_ip != tunnel['ip_address']:
                        tunnel_id = tunnel.get('id', tunnel['ip_address'])
                        tun_name = '%s-%s' % (tunnel_type, tunnel_id)
                        self.setup_tunnel_port(tun_name,
                                               tunnel['ip_address'],
                                               tunnel_type)
        except Exception as e:
            LOG.debug(_("Unable to sync tunnel IP %(local_ip)s: %(e)s"),
                      {'local_ip': self.local_ip, 'e': e})
            resync = True
        return resync

    def rpc_loop(self, polling_manager=None):
        if not polling_manager:
            polling_manager = polling.AlwaysPoll()

        sync = True
        ports = set()
        ancillary_ports = set()
        tunnel_sync = True
        while True:
            try:
                start = time.time()
                port_stats = {'regular': {'added': 0, 'removed': 0},
                              'ancillary': {'added': 0, 'removed': 0}}
                LOG.debug(_("Agent rpc_loop - iteration:%d started"),
                          self.iter_num)
                if sync:
                    LOG.info(_("Agent out of sync with plugin!"))
                    ports.clear()
                    ancillary_ports.clear()
                    sync = False
                    polling_manager.force_polling()

                # Notify the plugin of tunnel IP
                if self.enable_tunneling and tunnel_sync:
                    LOG.info(_("Agent tunnel out of sync with plugin!"))
                    tunnel_sync = self.tunnel_sync()
                if polling_manager.is_polling_required:
                    LOG.debug(_("Agent rpc_loop - iteration:%(iter_num)d - "
                                "starting polling. Elapsed:%(elapsed).3f"),
                              {'iter_num': self.iter_num,
                               'elapsed': time.time() - start})
                    port_info = self.update_ports(ports)
                    LOG.debug(_("Agent rpc_loop - iteration:%(iter_num)d - "
                                "port information retrieved. "
                                "Elapsed:%(elapsed).3f"),
                              {'iter_num': self.iter_num,
                               'elapsed': time.time() - start})
                    # notify plugin about port deltas
                    if port_info:
                        LOG.debug(_("Agent loop has new devices!"))
                        # If treat devices fails - must resync with plugin
                        sync = self.process_network_ports(port_info)
                        LOG.debug(_("Agent rpc_loop - iteration:%(iter_num)d -"
                                    "ports processed. Elapsed:%(elapsed).3f"),
                                  {'iter_num': self.iter_num,
                                   'elapsed': time.time() - start})
                        ports = port_info['current']
                        port_stats['regular']['added'] = (
                            len(port_info.get('added', [])))
                        port_stats['regular']['removed'] = (
                            len(port_info.get('removed', [])))
                    # Treat ancillary devices if they exist
                    if self.ancillary_brs:
                        port_info = self.update_ancillary_ports(
                            ancillary_ports)
                        LOG.debug(_("Agent rpc_loop - iteration:%(iter_num)d -"
                                    "ancillary port info retrieved. "
                                    "Elapsed:%(elapsed).3f"),
                                  {'iter_num': self.iter_num,
                                   'elapsed': time.time() - start})

                        if port_info:
                            rc = self.process_ancillary_network_ports(
                                port_info)
                            LOG.debug(_("Agent rpc_loop - iteration:"
                                        "%(iter_num)d - ancillary ports "
                                        "processed. Elapsed:%(elapsed).3f"),
                                      {'iter_num': self.iter_num,
                                       'elapsed': time.time() - start})
                            ancillary_ports = port_info['current']
                            port_stats['ancillary']['added'] = (
                                len(port_info.get('added', [])))
                            port_stats['ancillary']['removed'] = (
                                len(port_info.get('removed', [])))
                            sync = sync | rc

                    polling_manager.polling_completed()

            except Exception:
                LOG.exception(_("Error in agent event loop"))
                sync = True
                tunnel_sync = True

            # sleep till end of polling interval
            elapsed = (time.time() - start)
            LOG.debug(_("Agent rpc_loop - iteration:%(iter_num)d "
                        "completed. Processed ports statistics: "
                        "%(port_stats)s. Elapsed:%(elapsed).3f"),
                      {'iter_num': self.iter_num,
                       'port_stats': port_stats,
                       'elapsed': elapsed})
            if (elapsed < self.polling_interval):
                time.sleep(self.polling_interval - elapsed)
            else:
                LOG.debug(_("Loop iteration exceeded interval "
                            "(%(polling_interval)s vs. %(elapsed)s)!"),
                          {'polling_interval': self.polling_interval,
                           'elapsed': elapsed})
            self.iter_num = self.iter_num + 1

    def daemon_loop(self):
        with polling.get_polling_manager(
                self.minimize_polling,
                self.root_helper,
                self.ovsdb_monitor_respawn_interval) as pm:

            self.rpc_loop(polling_manager=pm)


def check_ovs_version(min_required_version, root_helper):
    LOG.debug(_("Checking OVS version for VXLAN support"))
    installed_klm_version = ovs_lib.get_installed_ovs_klm_version()
    installed_usr_version = ovs_lib.get_installed_ovs_usr_version(root_helper)
    # First check the userspace version
    if installed_usr_version:
        if dist_version.StrictVersion(
                installed_usr_version) < dist_version.StrictVersion(
                min_required_version):
            LOG.error(_('Failed userspace version check for Open '
                        'vSwitch with VXLAN support. To use '
                        'VXLAN tunnels with OVS, please ensure '
                        'the OVS version is %s '
                        'or newer!'), min_required_version)
            sys.exit(1)
        # Now check the kernel version
        if installed_klm_version:
            if dist_version.StrictVersion(
                    installed_klm_version) < dist_version.StrictVersion(
                    min_required_version):
                LOG.error(_('Failed kernel version check for Open '
                            'vSwitch with VXLAN support. To use '
                            'VXLAN tunnels with OVS, please ensure '
                            'the OVS version is %s or newer!'),
                          min_required_version)
                raise SystemExit(1)
        else:
            LOG.warning(_('Cannot determine kernel Open vSwitch version, '
                          'please ensure your Open vSwitch kernel module '
                          'is at least version %s to support VXLAN '
                          'tunnels.'), min_required_version)
    else:
        LOG.warning(_('Unable to determine Open vSwitch version. Please '
                      'ensure that its version is %s or newer to use VXLAN '
                      'tunnels with OVS.'), min_required_version)
        raise SystemExit(1)


def create_agent_config_map(config):
    """Create a map of agent config parameters.

    :param config: an instance of cfg.CONF
    :returns: a map of agent configuration parameters
    """
    try:
        bridge_mappings = q_utils.parse_mappings(config.RYU.bridge_mappings)
    except ValueError as e:
        raise ValueError(_("Parsing bridge_mappings failed: %s.") % e)

    kwargs = dict(
        integ_br=config.RYU.integration_bridge,
        tun_br=config.RYU.tunnel_bridge,
        local_ip=config.RYU.local_ip,
        bridge_mappings=bridge_mappings,
        root_helper=config.AGENT.root_helper,
        polling_interval=config.AGENT.polling_interval,
        minimize_polling=config.AGENT.minimize_polling,
        tunnel_types=config.AGENT.tunnel_types,
        veth_mtu=config.AGENT.veth_mtu,
        l2_population=False,
        ovsdb_monitor_respawn_interval=constants.DEFAULT_RYUDBMON_RESPAWN,
    )

    # If enable_tunneling is TRUE, set tunnel_type to default to GRE
    if config.RYU.enable_tunneling and not kwargs['tunnel_types']:
        kwargs['tunnel_types'] = [p_const.TYPE_GRE]

    # Verify the tunnel_types specified are valid
    for tun in kwargs['tunnel_types']:
        if tun not in constants.TUNNEL_NETWORK_TYPES:
            msg = _('Invalid tunnel type specificed: %s'), tun
            raise ValueError(msg)
        if not kwargs['local_ip']:
            msg = _('Tunneling cannot be enabled without a valid local_ip.')
            raise ValueError(msg)

    return kwargs
