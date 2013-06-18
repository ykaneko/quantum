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

import sys

from oslo.config import cfg

from quantumclient.common import exceptions as exc
from quantumclient.common import utils
from quantumclient.shell import env, QuantumShell, QUANTUM_API_VERSION

from quantum.agent.common import config
from quantum.fakevm import rpc as fakevm_rpc
from quantum.common import topics


COMMAND_V2 = {
    'create-port': utils.import_class('quantum.fakevm.commands.CreatePort'),
    'delete-port': utils.import_class('quantum.fakevm.commands.DeletePort'),
    'migrate': utils.import_class('quantum.fakevm.commands.Migrate'),
    'plug': utils.import_class('quantum.fakevm.commands.Plug'),
    'unplug': utils.import_class('quantum.fakevm.commands.Unplug'),
    'unplug-all-host':
    utils.import_class('quantum.fakevm.commands.UnplugAllHost'),
    'exec': utils.import_class('quantum.fakevm.commands.ExecCommand'),
}
COMMANDS = {'2.0': COMMAND_V2}


class QuantumFakeVMShell(QuantumShell):
    def __init__(self, api_version):
        super(QuantumFakeVMShell, self).__init__(api_version)
        for k, v in COMMANDS[api_version].items():
            self.command_manager.add_command(k, v)

    def build_option_parser(self, description, version):
        parser = super(QuantumFakeVMShell, self).build_option_parser(
            description, version)
        parser.add_argument(
            '--config-file',
            default=env('QUANTUM_FAKEVM_CONFIG_FILE'),
            help='Config file for fakevm shell ')
        return parser

    def initialize_app(self, argv):
        super(QuantumFakeVMShell, self).initialize_app(argv)
        if not self.options.config_file:
            raise exc.CommandError(
                "You must provide a config file for bridge -"
                " either --config-file or env[QUANTUM_FAKEVM_CONFIG_FILE]")
        cfg.CONF(['--config-file', self.options.config_file])
        config.setup_logging(cfg.CONF)

        self.fakevm_rpcapi = fakevm_rpc.FakeVMRpcApi(topics.FAKEVM_AGENT)


def main(argv=None):
    return QuantumFakeVMShell(QUANTUM_API_VERSION).run(argv or sys.argv[1:])
