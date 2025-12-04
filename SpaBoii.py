import socket
import threading
import queue
import io
import time
from enum import Enum
from levven_packet import LevvenPacket  
import proto.spa_live_pb2 as SpaLive
import proto.SpaCommand_pb2 as SpaCommand
import proto.SpaInformation_pb2 as SpaInformation
import proto.spa_configuration_pb2 as SpaConfiguration
# import proto.SpaOnzen_pb2 as SpaOnzen # Disabled for safety

from HA_auto_mqtt import init as HA_init

from API.BL.producer import Producer
from API.BL.consumer import Consumer

global producer, consumer
global debug

debug=False

# --- ADDED GLOBALS & CONSTANTS ---
global_ph_level = None
global_orp_level = None
global_pack_serial = None

ORP_ID = b'\x10'
PH_ID = b'\x18'
# ---------------------------------

class MessageType(Enum):
    LIVE = 0x00
    COMMAND = 0x01
    PING = 0x0A
    INFORMATION = 0x30
    CONFIGURATION = 0x03
    ONZEN_SETTINGS = 0x32

def get_message_title(value):
    try:
        return MessageType(value).name.title()
    except ValueError:
        return f"\033[41;97mUnknown message type {value}\033[0m"

cmd_queue = queue.Queue()
message_queue = queue.Queue()
response_queue = queue.Queue()

producer = Producer(message_queue, response_queue, cmd_queue)
consumer = Consumer(message_queue, response_queue, cmd_queue)

sensors, spa_ip = HA_init(producer)
consumer.start()

def find_sensor_by_name(sensors_list, name):
    for sensor_name, sensor_obj in sensors_list:
        if sensor_name == name:
            return sensor_obj
    return None

state = 0
temp1 = temp2 = temp3 = 0
i = 0
packet = LevvenPacket()
debug=True

def PingSpa(client, message_type = MessageType.LIVE.value):
    if debug:
        print(f"Sending {get_message_title(message_type)} ping with no content, type 0x{message_type:02X}:")
    pckt = LevvenPacket(message_type, bytearray())
    pack = LevvenToBytes(pckt)
    client.sendall(pack)

def LevvenToBytes(pckt):
    pack = pckt.serialize()
    if debug:
        hex_representation = ' '.join(f'{byte:02X}' for byte in pack)
        print(f"Serialized packet in hex ({len(pack)}): {hex_representation}")
    return pack

def read_and_process_packets(net_stream):
    ms = io.BytesIO()
    data = bytearray(2048)
    while True:
        num_bytes_read = net_stream.readinto(data)
        if num_bytes_read == 0: break
        ms.write(data[:num_bytes_read])
        if num_bytes_read < len(data): break
    bytes_data = ms.getvalue()
    for item in bytes_data:
        handle_packets(item)

def get_int(b1, b2, b3, b4): return (b1 << 24) | (b2 << 16) | (b3 << 8) | b4
def get_short(b1, b2): return (b1 << 8) | b2
def to_signed_byte(value): return value - 256 if value > 127 else value

def handle_packets(b):
    global state, temp1, temp2, temp3, i, packet
    rawByte = b
    b = to_signed_byte(b)
    try:
        if state == 1: state = 2 if rawByte == 0xAD else 0
        elif state == 2: state = 3 if rawByte == 0x1D else 0
        elif state == 3: state = 4 if rawByte == 0x3A else 0
        elif state == 4: temp1 = b; state = 5
        elif state == 5: temp2 = b; state = 6
        elif state == 6: temp3 = b; state = 7
        elif state == 7: packet.checksum = get_int(temp1, temp2, temp3, b); state = 8
        elif state == 8: temp1 = b; state = 9
        elif state == 9: temp2 = b; state = 10
        elif state == 10: temp3 = b; state = 11
        elif state == 11: packet.sequence_number = get_int(temp1, temp2, temp3, b); state = 12
        elif state == 12: temp1 = b; state = 13
        elif state == 13: temp2 = b; state = 14
        elif state == 14: temp3 = b; state = 15
        elif state == 15: packet.optional = get_int(temp1, temp2, temp3, b); state = 16
        elif state == 16: temp3 = b; state = 17
        elif state == 17: packet.type = get_short(temp3, b); state = 18
        elif state == 18: temp3 = b; state = 19
        elif state == 19:
            i = 0
            packet.size = get_short(temp3, b)
            packet.payload = bytearray(packet.size)
            if packet.size == 0: receive(packet); state = 0; return
            state = 20
        elif state == 20:
            packet.payload[i] = b & 0xFF
            i += 1
            if i >= packet.size: receive(packet); state = 0
        else: packet = LevvenPacket(); state = 1 if rawByte == 0xAB else 0
    except Exception: state = 0

def process_packet(packet):
    global debug, global_ph_level, global_orp_level, global_pack_serial, sensors, producer
    try:
        if debug and packet.type != MessageType.PING.value:
            print(f"Packet Type: {packet.type}/0x{packet.type:02X} - {get_message_title(packet.type)}")

        if packet.type == MessageType.PING.value: return
        
        if packet.type == MessageType.ONZEN_SETTINGS.value:
            if debug: print(f"\n{get_message_title(packet.type)}: Parsing Settings...")
            bytes_result = bytes(packet.payload)
            new_state = None
            if b'\x8f\x05' in bytes_result or b'\x85\x05' in bytes_result: new_state = "Mid"
            elif b'\xf3\x05' in bytes_result or b'\xe9\x05' in bytes_result: new_state = "High"
            elif b'\xab\x04' in bytes_result or b'\xa1\x04' in bytes_result: new_state = "Low"
            
            if new_state:
                if debug: print(f"--> Onzen Settings Update Detected: {new_state}")
                client = None
                cl_obj = find_sensor_by_name(sensors, "ClRange")
                if cl_obj:
                    if hasattr(cl_obj, 'mqtt_client'): client = cl_obj.mqtt_client
                    elif hasattr(cl_obj, '_mqtt_client'): client = cl_obj._mqtt_client
                if client is None:
                    temp_obj = find_sensor_by_name(sensors, "Temperature")
                    if temp_obj:
                        if hasattr(temp_obj, 'mqtt_client'): client = temp_obj.mqtt_client
                        elif hasattr(temp_obj, '_mqtt_client'): client = temp_obj._mqtt_client
                if client:
                    try:
                        topic = "hmd/select/SPABoii-ClRange/state"
                        client.publish(topic, new_state, retain=True)
                        if debug: print(f"   ✅ Published to MQTT: {topic} -> {new_state}")
                    except Exception as e: print(f"   🛑 Publish Exception: {e}")
            else:
                if debug: print("--> Onzen Settings received, but no matching range found.")
            return

        pack=LevvenToBytes(packet)

    except Exception as e:
        if debug: print(f"Error during packet serialization: {e}")
        pack=None
        return

    if pack!=None:
        if packet.type == MessageType.INFORMATION.value:
            if debug: print(f"\n{get_message_title(packet.type)}:\n")
            bytes_result = bytes(packet.payload)
            spa_information = SpaInformation.spa_information()
            spa_information.ParseFromString(bytes_result)
            
            if spa_information.pack_serial_number:
                global_pack_serial = spa_information.pack_serial_number
                if debug: print(f"--> Captured Pack Serial: {global_pack_serial}")

            if len(bytes_result) >= 100:
                if debug: print("--> Full status packet received. Parsing for pH/ORP...")
                orp_index = bytes_result.find(ORP_ID)
                if orp_index != -1 and len(bytes_result) > orp_index + 5:
                    expected_ph_index = orp_index + 3
                    if bytes_result[expected_ph_index] == PH_ID[0]:
                        val_bytes_orp = bytes_result[orp_index + 1 : orp_index + 3]
                        raw_val_orp = int.from_bytes(val_bytes_orp, 'little')
                        global_orp_level = raw_val_orp / 2.0
                        if debug: print(f"  (Raw Parse) 🌡️ ORP: {global_orp_level} mV (Raw: {raw_val_orp})")
                        
                        val_bytes_ph = bytes_result[expected_ph_index + 1 : expected_ph_index + 3]
                        raw_val_ph = int.from_bytes(val_bytes_ph, 'little')
                        global_ph_level = raw_val_ph / 200.0
                        if debug: print(f"  (Raw Parse) 🧪 pH: {global_ph_level} (Raw: {raw_val_ph})")

            if debug:
                print(f"--- FULL INFORMATION PACKET ---\n{spa_information}\n---------------------------------")
                print(f"Pack Serial Number: {spa_information.pack_serial_number}")
            
            live_json={ "PH": global_ph_level, "ORP": global_orp_level }
            for name, sensor in sensors:
                if name=="PH":
                    ph_val = live_json.get("PH")
                    if ph_val is not None: sensor.set_state(ph_val)
                if name=="ORP":
                    orp_val = live_json.get("ORP")
                    if orp_val is not None: sensor.set_state(orp_val)
            return
        
        elif packet.type == MessageType.CONFIGURATION.value:
            if debug:
                print(f"\n{get_message_title(packet.type)}:\n")
                bytes_result = bytes(packet.payload)
                spa_configuration = SpaConfiguration.spa_configuration()
                spa_configuration.ParseFromString(bytes_result)
                print(f"--- FULL CONFIGURATION PACKET ---\n{spa_configuration}\n---------------------------------")
                print(f"Exhaust Fan: {spa_configuration.exhaust_fan}")
                print(f"Breaker Size: {spa_configuration.breaker_size}")
                print(f"Fogger: {spa_configuration.fogger}")
            return
        
        elif packet.type == MessageType.LIVE.value:
            if debug: print(f"\n{get_message_title(packet.type)}:\n")
            bytes_result = bytes(packet.payload)
            spa_live = SpaLive.spa_live()
            spa_live.ParseFromString(bytes_result)
            
            if debug:
                print(f"--- FULL LIVE PACKET ---\n{spa_live}\n---------------------------------")
                print(f"Live Temperature: {spa_live.temperature_fahrenheit}°F")
                print(f"Setpoint Temperature: {spa_live.temperature_setpoint_fahrenheit}°F")
                print(f"Filter: {SpaLive.FILTER_STATUS.Name(spa_live.filter)}")
                print(f"Onzen: {spa_live.onzen}")
                print(f"Ozone: {SpaLive.OZONE_STATUS.Name(spa_live.ozone).lstrip('OZONE_')}")
                print(f"pH Level: {global_ph_level}")
                print(f"ORP Level: {global_orp_level} mV")
            
            live_json={
                "SetPoint_F": spa_live.temperature_setpoint_fahrenheit,
                "Temperature_F": spa_live.temperature_fahrenheit,
                "Filter": SpaLive.FILTER_STATUS.Name(spa_live.filter),
                "Onzen": spa_live.onzen, 
                "Ozone": SpaLive.OZONE_STATUS.Name(spa_live.ozone).lstrip('OZONE_'),
                "Blower 1": SpaLive.PUMP_STATUS.Name(spa_live.blower_1),
                "Blower 2": SpaLive.PUMP_STATUS.Name(spa_live.blower_2),
                "Pump 1": SpaLive.PUMP_STATUS.Name(spa_live.pump_1),
                "Pump 2": SpaLive.PUMP_STATUS.Name(spa_live.pump_2),
                "Pump 3": SpaLive.PUMP_STATUS.Name(spa_live.pump_3),
                "Heater 1": SpaLive.HEATER_STATUS.Name(spa_live.heater_1),
                "Heater 2": SpaLive.HEATER_STATUS.Name(spa_live.heater_2),
                "Lights": spa_live.lights,
                "All On": spa_live.all_on,
                "Heater ADC": spa_live.heater_adc,
                "Current ADC": spa_live.current_adc,
                }
            
            status=producer.send_message(live_json, "SPABoii.Live")

            for name, sensor in sensors:
                if name=="Temperature":
                    temp=live_json.get("Temperature_F")
                    if temp is not None: sensor.set_state(temp)
                if name=="SetPoint":
                    temp=live_json.get("SetPoint_F")
                    if temp is not None and temp > 30: sensor.set_value(round(temp,1))
                if name=="Heater1":
                    heaterStatus = live_json.get("Heater 1")
                    if heaterStatus == "HEATING" or heaterStatus == "WARMUP": sensor.on()
                    else: sensor.off()
                if name=="Pump1": sensor.mqtt_client.publish("hmd/select/SPABoii-Pump1/state", live_json.get("Pump 1"), False)
                if name=="Heater2":
                    heaterStatus = live_json.get("Heater 2")
                    if heaterStatus == "HEATING" or heaterStatus == "WARMUP": sensor.on()
                    else: sensor.off()
                if name=="Lights":
                    if live_json.get("Lights"): sensor.on()
                    else: sensor.off()
                if name=="Pump2":
                    if live_json.get("Pump 2") == "OFF": sensor.off()
                    else: sensor.on()
                if name=="Pump3":
                    if live_json.get("Pump 3") == "OFF": sensor.off()
                    else: sensor.on()
                if name=="Blower1":
                    if live_json.get("Blower 1") == "OFF": sensor.off()
                    else: sensor.on()
                if name=="Blower2":
                    if live_json.get("Blower 2") == "OFF": sensor.off()
                    else: sensor.on()
                if name == "FilterStatus": sensor.set_state(live_json.get("Filter"))
                if name == "OzoneStatus": sensor.set_state(live_json.get("Ozone"))
                if name == "HeaterADC": sensor.set_state(live_json.get("Heater ADC"))
                if name == "CurrentADC": sensor.set_state(live_json.get("Current ADC"))
                if name == "Heater1Status": sensor.set_state(live_json.get("Heater 1"))
                if name == "Heater2Status": sensor.set_state(live_json.get("Heater 2"))
            return

def receive(packet):
    process_packet(packet)

def send_packet_with_debug(spaIP, sensors, connection_sensor):
    global debug, global_pack_serial
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((spaIP, 65534))
    client.settimeout(5.0) 
    if connection_sensor: connection_sensor.on()
    i=0
    start_time = time.time()
    while True:
        spacmd = SpaCommand.spa_command()
        cmd_sent = False
        try: cmd=producer.cmd_queue.get(timeout=2)
        except queue.Empty: cmd=None
        
        if cmd!=None:
            action=cmd["CMD"]
            closeservice=action.get("CloseService")
            newSetpointF=action.get("SetPoint")
            pump1_cmd = action.get("pump1")
            pump2_cmd = action.get("pump2")
            pump3_cmd = action.get("pump3")
            lights_cmd = action.get("lights")
            blower1_cmd = action.get("blower1")
            blower2_cmd = action.get("blower2")
            boost_cmd = action.get("boost")
            cl_range_cmd = action.get("cl_range")

            if closeservice != None: break
            
            if newSetpointF!=None:
                print(f"Setpoint: {newSetpointF}°F")
                spacmd.set_temperature_setpoint_fahrenheit=int(newSetpointF)
                cmd_sent = True
            
            if pump1_cmd != None:
                print(f"Pump 1 Command: {pump1_cmd}")
                if pump1_cmd == "OFF": spacmd.set_pump_1 = 0
                elif pump1_cmd == "LOW": spacmd.set_pump_1 = 1
                elif pump1_cmd == "HIGH": spacmd.set_pump_1 = 2
                cmd_sent = True

            if pump2_cmd != None:
                print(f"Pump 2 Command: {pump2_cmd}")
                spacmd.set_pump_2 = 2 if pump2_cmd == "ON" else 0
                cmd_sent = True

            if pump3_cmd != None:
                print(f"Pump 3 Command: {pump3_cmd}")
                spacmd.set_pump_3 = 2 if pump3_cmd == "ON" else 0
                cmd_sent = True

            if lights_cmd != None:
                print(f"Lights Command: {lights_cmd}")
                spacmd.set_lights = 1 if lights_cmd == "ON" else 0
                cmd_sent = True

            if blower1_cmd != None:
                print(f"Blower 1 Command: {blower1_cmd}")
                spacmd.set_blower_1 = 2 if blower1_cmd == "ON" else 0
                cmd_sent = True

            if blower2_cmd != None:
                print(f"Blower 2 Command: {blower2_cmd}")
                spacmd.set_blower_2 = 2 if blower2_cmd == "ON" else 0
                cmd_sent = True
            
            if boost_cmd != None:
                print(f"Boost Command: {boost_cmd}")
                spacmd.set_onzen = 1 
                cmd_sent = True

            # --- Cl Range LOGIC (SAFE MODE: OPTIMISTIC UPDATE ONLY) ---
            if cl_range_cmd != None:
                print(f"Cl Range Command: {cl_range_cmd}")
                print("⚠️ Write Command disabled for Cl Range safety.")
                # Optimistic MQTT Update Only
                client_mqtt = None
                cl_obj = find_sensor_by_name(sensors, "ClRange")
                if cl_obj:
                    if hasattr(cl_obj, 'mqtt_client'): client_mqtt = cl_obj.mqtt_client
                    elif hasattr(cl_obj, '_mqtt_client'): client_mqtt = cl_obj._mqtt_client
                if client_mqtt is None:
                    temp_obj = find_sensor_by_name(sensors, "Temperature")
                    if temp_obj:
                        if hasattr(temp_obj, 'mqtt_client'): client_mqtt = temp_obj.mqtt_client
                        elif hasattr(temp_obj, '_mqtt_client'): client_mqtt = temp_obj._mqtt_client
                if client_mqtt:
                    try:
                        topic = "hmd/select/SPABoii-ClRange/state"
                        client_mqtt.publish(topic, cl_range_cmd, retain=True)
                        if debug: print(f"   ✅ Optimistic State Update: {topic} -> {cl_range_cmd}")
                    except Exception as e: print(f"   ⚠️ Optimistic Publish Warning: {e}")
            # ---------------------------------------------------------

            if cmd_sent:
                buffer=spacmd.SerializeToString()
                pckt = LevvenPacket(MessageType.COMMAND.value, buffer)
                if debug: print(f"Sending Command Packet Type: {pckt.type}/0x{pckt.type:02X}")
                pack = LevvenToBytes(pckt)
                client.sendall(pack)
                time.sleep(0.5)

        if i  == 0: PingSpa(client, MessageType.CONFIGURATION.value)
        elif i  == 4: PingSpa(client, MessageType.INFORMATION.value)
        i += 1
        
        if i % 4 == 0: PingSpa(client, MessageType.INFORMATION.value)
        else: PingSpa(client, MessageType.LIVE.value)

        temp = bytearray(2048)
        try:
            temp = client.recv(2048)
        except socket.timeout: continue 
        except Exception as e:
            elapsed_minutes = (time.time() - start_time) / 60 /60
            print(f"Connection lost after {elapsed_minutes:.2f} hours")
            if connection_sensor: connection_sensor.off()
            client.close()
            print("Restarting")
            break

        with io.BytesIO(temp) as net_stream:
            read_and_process_packets(net_stream)
                
    client.close()

while True:
    connection_sensor = find_sensor_by_name(sensors, "ConnectionStatus")
    if not spa_ip:
        print("Error: spa_ip not found in settings.yaml. Exiting.")
        if connection_sensor: connection_sensor.off()
        break
    try:
        print (f"Spa IP: {spa_ip}")
        send_packet_with_debug(spa_ip, sensors, connection_sensor)
    except KeyboardInterrupt:
        print("\nCtrl-C detected. Exiting gracefully...")
        if connection_sensor: connection_sensor.off()
        break 
    except Exception as e:
        print(f"An error occurred: {e}. Restarting...")
        if connection_sensor: connection_sensor.off()
        time.sleep(5)
        continue
