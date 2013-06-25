# Copyright (c) 2013 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock

from quantum.debug.fakevm import shell
from quantum.tests import base


class TestFakeVMShell(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMShell, self).setUp()
        self.addCleanup(mock.patch.stopall)

        self.mock_conf = mock.Mock()
        self.mock_cfg = mock.patch('quantum.debug.fakevm.shell.cfg').start()
        self.mock_cfg.CONF = self.mock_conf
        self.mock_config = mock.patch(
            'quantum.debug.fakevm.shell.config').start()
        self.mock_rpc = mock.patch(
            'quantum.debug.fakevm.shell.fakevm_rpc').start()

    def test_init(self):
        shell.QuantumFakeVMShell('2.0')

    def test_build_option_parser(self):
        fvshell = shell.QuantumFakeVMShell('2.0')

        parser = mock.Mock()
        with mock.patch(
            'quantumclient.shell.QuantumShell.build_option_parser',
            return_value=parser) as mock_build_option_parser:
            rc = fvshell.build_option_parser('desc', 'ver')

        mock_build_option_parser.assert_has_calls([
            mock.call('desc', 'ver')
        ])
        self.assertEqual(parser.add_argument.call_count, 1)
        self.assertEqual(rc, parser)

    def test_initialize_app(self):
        fvshell = shell.QuantumFakeVMShell('2.0')

        fvshell.options = mock.Mock()
        with mock.patch('quantumclient.shell.QuantumShell.initialize_app') as (
                mock_initialize_app):
            fvshell.initialize_app('argv')

        mock_initialize_app.assert_has_calls([
            mock.call('argv')
        ])
        self.mock_cfg.assert_has_calls([
            mock.call.CONF(['--config-file', fvshell.options.config_file])
        ])
        self.mock_config.assert_has_calls([
            mock.call.setup_logging(self.mock_conf)
        ])
        self.mock_rpc.assert_has_calls([
            mock.call.FakeVMRpcApi('fakevm_agent')
        ])
