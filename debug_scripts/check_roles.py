#!/usr/bin/env python3
from meshtastic import config_pb2

print('Device Role enum values:')
for name, value in config_pb2.Config.DeviceConfig.Role.items():
    print(f'{name} = {value}')
