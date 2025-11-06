#!/usr/bin/env python3
"""Debug script to inspect node data and role values."""

import meshtastic
import meshtastic.serial_interface

# Connect to device
iface = meshtastic.serial_interface.SerialInterface()

print("\n=== NODE DATA INSPECTION ===\n")

if hasattr(iface, 'nodes') and iface.nodes:
    # First show nodes without role key
    print("\n=== NODES WITHOUT ROLE KEY ===")
    for node_id, node_data in iface.nodes.items():
        if 'user' in node_data:
            user = node_data['user']
            if 'role' not in user:
                print(f"  {user.get('longName', 'Unknown')} ({node_id})")
    
    # Show nodes with role=0
    print("\n\n=== NODES WITH ROLE=0 (should be CLIENT) ===")
    for node_id, node_data in iface.nodes.items():
        if 'user' in node_data:
            user = node_data['user']
            if 'role' in user and user['role'] == 0:
                print(f"  {user.get('longName', 'Unknown')} ({node_id}) - role={repr(user['role'])} type={type(user['role']).__name__}")
    
    # Show nodes with string roles
    print("\n\n=== NODES WITH STRING ROLES ===")
    for node_id, node_data in iface.nodes.items():
        if 'user' in node_data:
            user = node_data['user']
            if 'role' in user and isinstance(user['role'], str):
                print(f"  {user.get('longName', 'Unknown')} ({node_id}) - role={repr(user['role'])}")

iface.close()
