#!/usr/bin/env python3
import time
import sys
import meshtastic
import meshtastic.serial_interface
from meshtastic import BROADCAST_ADDR
from pubsub import pub

# ====== CONFIG ======
MESSAGE = "Test message. Attempt number {attempt}."
DEST = BROADCAST_ADDR  # Change to "!nodeID" to get ACK from that specific node
SEND_INTERVAL = 10  # Delay seconds between sends
SERIAL_PORT = None  # set explicitly if needed, e.g. "/dev/ttyUSB0"
# ====================


def on_connection(interface, topic=pub.AUTO_TOPIC):
    print(f"[INFO] Connected to Meshtastic device")
    try:
        node = interface.getMyNodeInfo()
        print(
            f"[INFO] Device is alive, node ID: {node['user']['id']}, shortName: {node['user']['shortName']}"
        )
    except Exception as e:
        print(f"[WARN] Could not query node info: {e}")


def on_disconnect(topic=pub.AUTO_TOPIC):
    print("[ERROR] Disconnected from device.")
    sys.exit(1)


def on_receive(packet, interface):
    """Monitor all received packets to see rebroadcasts"""
    packet_id = packet.get("id")
    from_id = packet.get("fromId", packet.get("from", "unknown"))
    to_id = packet.get("toId", packet.get("to", "unknown"))

    # Try to extract message content if it's a text message
    message_content = None
    decoded = packet.get("decoded", {})
    portnum = decoded.get("portnum", "unknown")

    # Skip logging for common non-text message types
    ignored_types = [
        "POSITION_APP",
        "TELEMETRY_APP",
        "ROUTING_APP",
        "NODEINFO_APP",
        "ADMIN_APP",
        "unknown",
    ]
    if portnum in ignored_types:
        return

    # Check if it's a text message
    if portnum == "TEXT_MESSAGE_APP" or portnum == 1:
        # Try to get the text directly
        message_content = decoded.get("text")
        # If not available, try decoding the payload
        if not message_content and "payload" in decoded:
            try:
                payload = decoded["payload"]
                if isinstance(payload, bytes):
                    message_content = payload.decode("utf-8")
                elif isinstance(payload, str):
                    message_content = payload
            except Exception:
                message_content = "<unable to decode>"

    if message_content:
        print(
            f"[RX] Received packet: id=0x{packet_id:08x}, from={from_id}, to={to_id}, message='{message_content}'"
        )
    else:
        # Only log non-ignored types
        print(
            f"[RX] Received packet: id=0x{packet_id:08x}, from={from_id}, to={to_id}, type={portnum}"
        )


def main():
    print("[INFO] Connecting to Meshtastic device...")
    try:
        iface = meshtastic.serial_interface.SerialInterface(devPath=SERIAL_PORT)
    except Exception as e:
        print(f"[FATAL] Could not open serial port: {e}")
        sys.exit(1)

    # Subscribe to relevant events
    pub.subscribe(on_connection, "meshtastic.connection.established")
    pub.subscribe(on_disconnect, "meshtastic.connection.lost")
    pub.subscribe(on_receive, "meshtastic.receive")  # Monitor all received packets

    # Give a second for connection events to fire
    time.sleep(2)

    # Confirm the device is alive
    try:
        info = iface.getMyNodeInfo()
        print(
            f"[INFO] Node connected: {info['user']['longName']} ({info['user']['shortName']})"
        )
    except Exception as e:
        print(f"[ERROR] Could not verify node: {e}")
        sys.exit(1)

    print(
        f"[INFO] Starting send loop: will send every {SEND_INTERVAL}s until ACK received."
    )

    ack_received = False
    sent_packet_id = None
    attempt_count = 0

    while not ack_received:
        try:
            attempt_count += 1
            # Create dynamic message with current attempt number
            current_message = MESSAGE.format(attempt=attempt_count)
            print(
                f"[SEND] Attempt #{attempt_count}: Sending message: '{current_message}' to {DEST}"
            )
            packet = iface.sendText(current_message, destinationId=DEST, wantAck=True)
            sent_packet_id = packet.get("id") if isinstance(packet, dict) else packet.id
            print(f"[SEND] Sent packet with ID: 0x{sent_packet_id:08x}")

            # Wait for ACK using the proper acknowledgment mechanism
            print("[INFO] Waiting for ACK/implicit ACK...")
            success = iface.waitForAckNak()

            if success:
                print(
                    f"[SUCCESS] ACK/implicit ACK received for packet 0x{sent_packet_id:08x}!"
                )
                ack_received = True
                break
            else:
                print(
                    f"[WARN] No ACK received within timeout for packet 0x{sent_packet_id:08x}, retrying in {SEND_INTERVAL}s..."
                )
                time.sleep(SEND_INTERVAL)

        except Exception as e:
            print(f"[ERROR] Failed to send: {e}")
            time.sleep(5)
            continue

    print("[SUCCESS] Message confirmed delivered. Exiting.")
    iface.close()


if __name__ == "__main__":
    main()
