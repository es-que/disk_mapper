#!/usr/bin/env python

#   Copyright 2013 Zynga Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

config = {
    'storage_server': 
                    [
                     'server_1_ip',
                     'server_2_ip',
                     'server_3_ip',
                    ],
    'zruntime': 
              {'username' : 'zbase', 
               'password' : 'zbase-passwd',
               'gameid' : 'zbase',
               'env' : 'prod',
               'mcs_key_name' : 'ACTIVE_MCS',
               'retries' : 60,
              },
    'params':
            {'poll_interval' : 5,
             'log_level' : 'info',
            },
}

