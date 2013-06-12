# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2013 Isaku Yamahata <yamahata at private email ne jp>
#                               <yamahata at valinux co jp>
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
# @author: Isaku Yamahata

import socket

from quantumclient.quantum.v2_0 import QuantumCommand

from quantum.openstack.common import log as logging
from quantum import context


_DEVICE_OWNER_FAKEVM = 'qfakevm:vmport:%s'


class FakeVMCommand(QuantumCommand):
    log = logging.getLogger(__name__ + '.FakeVMCommand')

    def get_quantum_client(self):
        return self.app.client_manager.quantum

    def get_fakevm_rpcapi(self):
        return self.app.fakevm_rpcapi

    def get_parser(self, prog_name):
        parser = super(FakeVMCommand, self).get_parser(prog_name)
        parser.add_argument('--host',
                            default=socket.gethostname(),
                            help='target host name')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)
        self.app.stdout.write(_('Unimplemented commands') + '\n')

    def _get_network(self, network_id):
        client = self.get_quantum_client()
        network = client.show_network(network_id)['network']
        return network

    def _create_port(self, network, instance_id):
        body = dict(port=dict(
            admin_state_up=True,
            network_id=network['id'],
            device_id=instance_id,
            # use device_id to store instanced_id
            device_owner=_DEVICE_OWNER_FAKEVM % instance_id,
            tenant_id=network['tenant_id'],
            fixed_ips=[dict(subnet_id=s_id) for s_id in network['subnets']]))
        port = self.get_quantum_client().create_port(body)['port']
        port['instance_id'] = instance_id
        return port

    def _delete_port(self, vif_uuid):
        self.get_quantum_client().delete_port(vif_uuid)

    def _show_port(self, vif_uuid):
        port = self.get_quantum_client().show_port(vif_uuid)['port']
        port['instance_id'] = port['device_owner'].split(':')[-1]
        return port

    def _plug(self, host, port, bridge_name=None):
        network_id = port['network_id']
        vif_uuid = port['id']
        instance_id = port['instance_id']
        mac_address = port['mac_address']
        fakevm_rpcapi = self.get_fakevm_rpcapi()
        ctx = context.get_admin_context_without_session()
        fakevm_rpcapi.plug(ctx, host,
                           instance_id, network_id, vif_uuid, mac_address,
                           bridge_name)

    def _unplug(self, host, port, bridge_name=None):
        network_id = port['network_id']
        vif_uuid = port['id']
        fakevm_rpcapi = self.get_fakevm_rpcapi()
        ctx = context.get_admin_context_without_session()
        fakevm_rpcapi.unplug(ctx, host, network_id, vif_uuid, bridge_name)


class CreatePort(FakeVMCommand):
    """create VM port"""

    log = logging.getLogger(__name__ + '.CreatePort')

    def get_parser(self, prog_name):
        parser = super(CreatePort, self).get_parser(prog_name)
        parser.add_argument('network_id', metavar='network_id', type=str,
                            help='ID of network')
        parser.add_argument('instance_id', metavar='instance_id', type=str,
                            help='ID of instance')
        parser.add_argument('bridge_name', metavar='bridge_name', type=str,
                            nargs='?', default=None,
                            help='bridge name to plug')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)
        host = parsed_args.host

        network = self._get_network(parsed_args.network_id)
        self.log.debug('network %s', network)

        port = self._create_port(network, parsed_args.instance_id)
        self.log.debug('port %s', port)

        self._plug(host, port, parsed_args.bridge_name)
        self.app.stdout.write(
            _('VM port created on %s: vif_uuid: %s '
              'mac: %s tenant_id: %s\nfixed_ips: %s\nnetowrk: %s\n') %
            (host, port['id'], port['mac_address'], port['tenant_id'],
             port['fixed_ips'], network))


class DeletePort(FakeVMCommand):
    """delete VM port"""

    log = logging.getLogger(__name__ + '.DeletePort')

    def get_parser(self, prog_name):
        parser = super(DeletePort, self).get_parser(prog_name)
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        parser.add_argument('bridge_name', metavar='bridge_name', type=str,
                            nargs='?', default=None,
                            help='bridge name to plug')
        return parser

    def run(self, parsed_args):
        # TODO:XXX use fanout to unplug on all hosts
        self.log.debug('run(%s)' % parsed_args)

        host = parsed_args.host
        vif_uuid = parsed_args.vif_uuid
        port = self._show_port(vif_uuid)

        self._unplug(host, port, parsed_args.bridge_name)
        self._delete_port(vif_uuid)
        self.app.stdout.write(_('VM port deleted : %s\n') %
                              parsed_args.vif_uuid)


class Migrate(FakeVMCommand):
    """Migrate vif"""

    log = logging.getLogger(__name__ + '.Migrate')

    def get_parser(self, prog_name):
        parser = super(Migrate, self).get_parser(prog_name)
        parser.add_argument('--src-bridge-name', metavar='src_bridge_name',
                            default=None,
                            help='bridge name to unplug on src host')
        parser.add_argument('--dst-bridge-name', metavar='dst_bridge_name',
                            default=None,
                            help='bridge name to plug on dst host')
        parser.add_argument('dst_host', metavar='dst_host', type=str,
                            default=None, help='destination host to migrate')
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        return parser

    def run(self, parsed_args):
        # TODO:XXX use fanout to unplug on all hosts
        self.log.debug('run(%s)' % parsed_args)

        dst_host = parsed_args.dst_host
        if not dst_host:
            raise ValueError('destination host is not specified')

        src_host = parsed_args.host
        if dst_host == src_host:
            raise ValueError('destination host must differ from current host '
                             '%s' % src_host)

        vif_uuid = parsed_args.vif_uuid
        port = self._show_port(vif_uuid)
        self._plug(dst_host, port, parsed_args.dst_bridge_name)
        # TODO: send GARP packet on dst_host
        # start dhcp client?

        self._unplug(src_host, port, parsed_args.src_bridge_name)
        self.app.stdout.write(_('VM migrate : %s %s -> %s\n') %
                              (vif_uuid, src_host, dst_host))


class Plug(FakeVMCommand):
    """plug vif"""

    log = logging.getLogger(__name__ + '.Plug')

    def get_parser(self, prog_name):
        parser = super(Plug, self).get_parser(prog_name)
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        parser.add_argument('bridge_name', metavar='bridge_name', type=str,
                            nargs='?', default=None,
                            help='bridge name to plug')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)

        host = parsed_args.host
        vif_uuid = parsed_args.vif_uuid
        port = self._show_port(vif_uuid)

        self._plug(host, port, parsed_args.bridge_name)
        self.app.stdout.write(
            _('VM port pluged on %s %s: vif_uuid: %s '
              'mac: %s tenant_id: %s instance_id %s\n'
              'fixed_ips: %s\n') %
            (host, parsed_args.bridge_name,
             vif_uuid, port['mac_address'], port['tenant_id'],
             port['instance_id'], port['fixed_ips']))


class Unplug(FakeVMCommand):
    """unplug vif"""

    log = logging.getLogger(__name__ + '.Unplug')

    def get_parser(self, prog_name):
        parser = super(Unplug, self).get_parser(prog_name)
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        parser.add_argument('bridge_name', metavar='bridge_name', type=str,
                            nargs='?', default=None,
                            help='bridge name to plug')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)

        host = parsed_args.host
        vif_uuid = parsed_args.vif_uuid
        port = self._show_port(vif_uuid)

        self._unplug(host, port, parsed_args.bridge_name)
        self.app.stdout.write(_('VM port unpluged on %s: %s %s\n') %
                              (host, vif_uuid, parsed_args.bridge_name))


class UnplugAllHost(FakeVMCommand):
    """unplug vif on all host"""

    log = logging.getLogger(__name__ + '.UnplugAllHost')

    def get_parser(self, prog_name):
        parser = super(UnplugAllHost, self).get_parser(prog_name)
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        parser.add_argument('bridge_name', metavar='bridge_name', type=str,
                            nargs='?', default=None,
                            help='bridge name to plug')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)

        vif_uuid = parsed_args.vif_uuid
        port = self._show_port(vif_uuid)
        network_id = port['network_id']

        fakevm_rpcapi = self.get_fakevm_rpcapi()
        ctx = context.get_admin_context_without_session()
        fakevm_rpcapi.unplug_all_host(ctx, network_id, vif_uuid,
                                      parsed_args.bridge_name)
        self.app.stdout.write(_('VM port unpluged on all host: %s %s %s\n') %
                              (network_id, vif_uuid, parsed_args.bridge_name))


class ExecCommand(FakeVMCommand):
    """execute on the interface"""

    log = logging.getLogger(__name__ + '.ExecCommand')

    def get_parser(self, prog_name):
        parser = super(ExecCommand, self).get_parser(prog_name)
        parser.add_argument('vif_uuid', metavar='vif_uuid', type=str,
                            help='ID of vif')
        parser.add_argument('command', metavar='command',
                            nargs='?', default=None,
                            help='command to execute for vif')
        return parser

    def run(self, parsed_args):
        self.log.debug('run(%s)' % parsed_args)

        fakevm_rpcapi = self.get_fakevm_rpcapi()
        ctx = context.get_admin_context_without_session()
        result = fakevm_rpcapi.exec_command(
            ctx, parsed_args.host, parsed_args.vif_uuid, parsed_args.command)
        self.app.stdout.write(_('VM port executeon %s: %s %s\n%s\n') %
                              (parsed_args.host, parsed_args.vif_uuid,
                               parsed_args.command, result))
