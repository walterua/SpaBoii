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

from HA_auto_mqtt import init as HA_init


from API.BL.producer import Producer
from API.BL.consumer import Consumer

global producer, consumer
global debug

debug=False

# --- ADDED GLOBALS & CONSTANTS ---
global_ph_level = None
global_orp_level = None

# These are the "ID" bytes for the values we want, from your analysis
ORP_ID = b'\x10'
PH_ID = b'\x18'
# ---------------------------------

class MessageType(Enum):
    LIVE = 0x00
    COMMAND = 0x01
    PING = 0x0A
    INFORMATION = 0x30
    CONFIGURATION = 0x03

def get_message_title(value):
    try:
        return MessageType(value).name.title()  # Convert name to title case
    except ValueError:
        return f"\033[41;97mUnknown message type {value}\033[0m"

# --- REMOVED F_to_C conversion function ---

# Create shared queues for messages and responses
cmd_queue = queue.Queue()
message_queue = queue.Queue()
response_queue = queue.Queue()

# Instantiate Producer and Consumer
producer = Producer(message_queue, response_queue, cmd_queue)
consumer = Consumer(message_queue, response_queue, cmd_queue)

# --- MODIFIED: Now receives spa_ip ---
# Initialize the sensors
sensors, spa_ip = HA_init(producer)
# -------------------------------------

# Start the consumer
consumer.start()

# --- NEW HELPER FUNCTION ---
def find_sensor_by_name(sensors_list, name):
    """Finds a sensor object in the list by its name."""
    for sensor_name, sensor_obj in sensors_list:
        if sensor_name == name:
            return sensor_obj
    return None
# ---------------------------

state = 0
temp1 = temp2 = temp3 = 0
i = 0
packet = LevvenPacket()
debug=False
debug=True

def PingSpa(client, message_type = MessageType.LIVE.value):
    if debug:
        print(f"Sending {get_message_title(message_type)} ping with no content, type 0x{message_type:02X}:")
    pckt = LevvenPacket(message_type, bytearray())  # Initialize with type 0 and an empty payload
    pack = LevvenToBytes(pckt)

    # Send the serialized packet over the TCP connection
    client.sendall(pack)

def LevvenToBytes(pckt):
    pack = pckt.serialize()  # Serialize the packet to bytes

    # Debug: Print the serialized packet as hex bytes
    if debug:
        hex_representation = ' '.join(f'{byte:02X}' for byte in pack)
        print(f"Serialized packet in hex ({len(pack)}): {hex_representation}")
    return pack

def get_spa():
    # This function is no longer used, but we keep it for reference
    # Create a UDP socket
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # Enable broadcasting
    client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # Prepare and send the broadcast message
    request_data = "Query,BlueFalls,".encode('ascii')
    client.sendto(request_data, ('<broadcast>', 9131))

    # Receive the response (no explicit bind needed)
    server_response_data, server_ep = client.recvfrom(4096)
    
    # Decode the response and output the server's address and data
    server_response = server_response_data.decode('ascii')
    if debug:
        print(f"Located SPA at {server_ep[0]}: {server_response}")

    # Close the client socket
    client.close()
    return server_ep[0]

def read_and_process_packets(net_stream):
    # Simulating MemoryStream using BytesIO
    ms = io.BytesIO()
    data = bytearray(2048)
    
    # Read from the network stream
    while True:
        num_bytes_read = net_stream.readinto(data)
        if num_bytes_read == 0:
            break
        ms.write(data[:num_bytes_read])
        if num_bytes_read < len(data):
            break

    # Get the full byte array from the memory stream
    bytes_data = ms.getvalue()
    hex_representation = ' '.join(f'{byte:02X}' for byte in bytes_data)
    # Process each byte by calling handle_packets
    for item in bytes_data:
        handle_packets(item)

def get_int(b1, b2, b3, b4):
    """Convert four bytes to an integer."""
    return (b1 << 24) | (b2 << 16) | (b3 << 8) | b4

def get_short(b1, b2):
    """Convert two bytes to a short."""
    return (b1 << 8) | b2

def get_int(b1, b2, b3, b4):
    """Convert four bytes to an integer."""
    return (b1 << 24) | (b2 << 16) | (b3 << 8) | b4

def get_short(b1, b2):
    """Convert two bytes to a short."""
    return (b1 << 8) | b2

def to_signed_byte(value):
    """Convert a byte (0-255) to a signed byte (-128 to 127)."""
    if value > 127:
        return value - 256
    return value

def handle_packets(b):
    global state, temp1, temp2, temp3, i, packet

    rawByte = b
    # Convert the input byte to a signed byte
    b = to_signed_byte(b)

    try:
        if state == 1:
            # Second byte of 4 byte magic number 0x AB AD 1D 3A
            state = 2 if rawByte == 0xAD else 0
            return
        elif state == 2:
            # Third byte of 4 byte magic number 0x AB AD 1D 3A
            state = 3 if rawByte == 0x1D else 0
            return
        elif state == 3:
            # Fourth byte of 4 byte magic number 0x AB AD 1D 3A
            state = 4 if rawByte == 0x3A else 0
            return
        elif state == 4:
            temp1 = b
            state = 5
            return
        elif state == 5:
            temp2 = b
            state = 6
            return
        elif state == 6:
            temp3 = b
            state = 7
            return
        elif state == 7:
            packet.checksum = get_int(temp1, temp2, temp3, b)
            state = 8
            return
        elif state == 8:
            temp1 = b
            state = 9
            return
        elif state == 9:
            temp2 = b
            state = 10
            return
        elif state == 10:
            temp3 = b
            state = 11
            return
        elif state == 11:
            packet.sequence_number = get_int(temp1, temp2, temp3, b)
            state = 12
            return
        elif state == 12:
            temp1 = b
            state = 13
            return
        elif state == 13:
            temp2 = b
            state = 14
            return
        elif state == 14:
            temp3 = b
            state = 15
            return
        elif state == 15:
            packet.optional = get_int(temp1, temp2, temp3, b)
            state = 16
            return
        elif state == 16:
            temp3 = b
            state = 17
            return
        elif state == 17:
            packet.type = get_short(temp3, b)
            state = 18
            return
        elif state == 18:
            temp3 = b
            state = 19
            return
        elif state == 19:
            i = 0
            packet.size = get_short(temp3, b)
            packet.payload = bytearray(packet.size)
            if packet.size == 0:
                receive(packet)
                state = 0
                return
            state = 20
            return
        elif state == 20:
            packet.payload[i] = b & 0xFF  # Convert to unsigned byte for storage
            i += 1
            if i >= packet.size:
                receive(packet)
                state = 0
            return
        else:
            packet = LevvenPacket()
            # First byte of 4 byte magic number 0x AB AD 1D 3A
            state = 1 if rawByte == 0xAB else 0
            return
    except Exception:
        state = 0


# --- NEW FUNCTION TO PROCESS PACKETS ---
def process_packet(packet):
    global debug, global_ph_level, global_orp_level, sensors
    try:
        if debug:
            print(f"Packet Type: {packet.type}/0x{packet.type:02X} - {get_message_title(packet.type)}")

        if packet.type == MessageType.PING.value:
            # Nothing to see here, don't decode or debug print the packet
            return
        
        pack=LevvenToBytes(packet)

    except Exception as e:
        if debug:
            print(f"Error during packet serialization: {e}")
        pack=None
        return

    if pack!=None:
        if packet.type == MessageType.INFORMATION.value:
            if debug:
                print(f"\n{get_message_title(packet.type)}:\n")
            bytes_result = bytes(packet.payload)
            spa_information = SpaInformation.spa_information()
            spa_information.ParseFromString(bytes_result)

            # --- NEW pH/ORP RAW PACKET PARSING ---
            # We run this on bytes_result (packet.payload)
            
            if len(bytes_result) < 100:
                if debug:
                    print("--> Short packet received (likely a ping or command ack). Ignoring for pH/ORP.")
            else:
                if debug:
                    print("--> Full status packet received. Parsing for pH/ORP...")
                
                orp_index = bytes_result.find(ORP_ID)
                
                if orp_index != -1 and len(bytes_result) > orp_index + 5:
                    expected_ph_index = orp_index + 3
                    
                    if bytes_result[expected_ph_index] == PH_ID[0]:
                        val_bytes_orp = bytes_result[orp_index + 1 : orp_index + 3]
                        raw_val_orp = int.from_bytes(val_bytes_orp, 'little')
                        global_orp_level = raw_val_orp / 2.0
                        if debug:
                            print(f"  (Raw Parse) 🌡️ ORP: {global_orp_level} mV (Raw: {raw_val_orp})")
                        
                        val_bytes_ph = bytes_result[expected_ph_index + 1 : expected_ph_index + 3]
                        raw_val_ph = int.from_bytes(val_bytes_ph, 'little')
                        global_ph_level = raw_val_ph / 200.0
                        if debug:
                            print(f"  (Raw Parse) 🧪 pH: {global_ph_level} (Raw: {raw_val_ph})")
                    else:
                        if debug:
                            print("--> Found ORP but PH ID was missing. Skipping parse.")
                else:
                    if debug:
                        print("--> Status packet did not contain expected ORP/PH data.")
            # --- END OF NEW PARSING ---


            if debug:
                print(f"--- FULL INFORMATION PACKET ---\n{spa_information}\n---------------------------------")
                print(f"Pack Serial Number: {spa_information.pack_serial_number}")

                try:
                    print(f"Product Code: {SpaInformation.PRODUCT_CODE.Name(spa_information.product_code)}")
                except ValueError:
                    print(f"Product Code: {spa_information.product_code} (Unknown ID)")
                
                try:
                    print(f"Spa Type: {SpaInformation.SPA_TYPE.Name(spa_information.spa_type)}")
                except ValueError:
                     print(f"Spa Type: {spa_information.spa_type} (Unknown ID)")
            
            # ----- SENSOR UPDATE LOGIC (FOR PH/ORP ONLY) -----
            
            live_json={ "PH": global_ph_level, "ORP": global_orp_level }
            
            for name, sensor in sensors:
                if name=="PH":
                    ph_val = live_json.get("PH")
                    if ph_val is not None:
                        if debug:
                            print(f"HA Sensor Name: PH, Value: {ph_val}")
                        sensor.set_state(ph_val)
                if name=="ORP":
                    orp_val = live_json.get("ORP")
                    if orp_val is not None:
                        if debug:
                            print(f"HA Sensor Name: ORP, Value: {orp_val}")
                        sensor.set_state(orp_val)
            
            return # Continue loop
        
        elif packet.type == MessageType.CONFIGURATION.value:
            if debug:
                print(f"\n{get_message_title(packet.type)}:\n")
                bytes_result = bytes(packet.payload)
                spa_configuration = SpaConfiguration.spa_configuration()
                spa_configuration.ParseFromString(bytes_result)

                print(f"--- FULL CONFIGURATION PACKET ---\n{spa_configuration}\n---------------------------------")
                
                try:
                    print(f"Power: {SpaConfiguration.PHASE.Name(spa_configuration.powerlines)}")
                except ValueError:
                    powerlines_val = spa_configuration.powerlines
                    if 'spa_information' in locals() and hasattr(spa_information, 'powerlines'):
                         powerlines_val = spa_information.powerlines
                    print(f"Power: {powerlines_val} (Unknown ID)")
                    
                print(f"Exhaust Fan: {spa_configuration.exhaust_fan}")
                print(f"Breaker Size: {spa_configuration.breaker_size}")
                print(f"Fogger: {spa_configuration.fogger}")
            return # Continue loop
        
        # ----- THIS BLOCK NOW HANDLES ALL MAIN SENSOR UPDATES -----
        elif packet.type == MessageType.LIVE.value:
            if debug:
                print(f"\n{get_message_title(packet.type)}:\n")
            bytes_result = bytes(packet.payload)
            hex_representation = ' '.join(f'{byte:02}' for byte in bytes_result)                
            spa_live = SpaLive.spa_live()
            spa_live.ParseFromString(bytes_result)
            
            if debug==True:
                print(f"--- FULL LIVE PACKET ---\n{spa_live}\n---------------------------------")
                
                print(f"Live Temperature: {spa_live.temperature_fahrenheit}°F")
                print(f"Setpoint Temperature: {spa_live.temperature_setpoint_fahrenheit}°F")
                print(f"Filter: {SpaLive.FILTER_STATUS.Name(spa_live.filter)}")
                print(f"Onzen: {spa_live.onzen}")
                print(f"Ozone: {SpaLive.OZONE_STATUS.Name(spa_live.ozone).lstrip('OZONE_')}")
                print(f"Blower 1: {SpaLive.PUMP_STATUS.Name(spa_live.blower_1)}")
                print(f"Blower 2: {SpaLive.PUMP_STATUS.Name(spa_live.blower_2)}")
                print(f"Pump 1: { SpaLive.PUMP_STATUS.Name(spa_live.pump_1) }")
                print(f"Pump 2: {SpaLive.PUMP_STATUS.Name(spa_live.pump_2)}")
                print(f"Pump 3: {SpaLive.PUMP_STATUS.Name(spa_live.pump_3) }")
                status_str = ', '.join(f"{name} = {value}" for value, name in SpaLive.HEATER_STATUS.items())
                print(f"Heater Status Options: {status_str}")
                print(f"Heater 1: {SpaLive.HEATER_STATUS.Name(spa_live.heater_1)}")
                print(f"Heater 2: {SpaLive.HEATER_STATUS.Name(spa_live.heater_2)}")
                print(f"Lights: {spa_live.lights}")
                print(f"All On: {spa_live.all_on}")
                print(f"Economy: {spa_live.economy}")
                print(f"Exhaust Fan: {spa_live.exhaust_fan}")
                print(f"Heater ADC: {spa_live.heater_adc}")
                print(f"Current ADC: {spa_live.current_adc}")
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

            # Update all sensors
            for name, sensor in sensors:
                if debug:
                    print(f"HA Sensor Name: {name}")
                
                if name=="Temperature":
                    temp=live_json.get("Temperature_F")
                    if temp is not None:
                        sensor.set_state(temp)
                
                if name=="SetPoint":
                    temp=live_json.get("SetPoint_F")
                    if temp is not None and temp > 30: # Check for F temp
                        temp=round(temp,1) # 1 decimal
                        sensor.set_value(temp)
                
                if name=="Heater1":
                    heaterStatus = live_json.get("Heater 1")
                    if heaterStatus == "HEATING" or heaterStatus == "WARMUP":
                        sensor.on()
                    else:
                        sensor.off()
                if name=="Pump1":
                    sensor.mqtt_client.publish("hmd/select/SPABoii-Pump1/state", live_json.get("Pump 1"), False)
        
                if name=="Heater2":
                    heaterStatus = live_json.get("Heater 2")
                    if heaterStatus == "HEATING" or heaterStatus == "WARMUP":
                        sensor.on()
                    else:
                        sensor.off()
                
                if name=="Lights":
                    if live_json.get("Lights"):
                        sensor.on()
                    else:
                        sensor.off()

                if name=="Pump2":
                    pump_status = live_json.get("Pump 2")
                    if pump_status == "OFF":
                        sensor.off()
                    else:
                        sensor.on() # Treat LOW or HIGH as ON

                if name=="Pump3":
                    pump_status = live_json.get("Pump 3")
                    if pump_status == "OFF":
                        sensor.off()
                    else:
                        sensor.on() # Treat LOW or HIGH as ON

                if name=="Blower1":
                    pump_status = live_json.get("Blower 1")
                    if pump_status == "OFF":
                        sensor.off()
                    else:
                        sensor.on() # Treat LOW or HIGH as ON

                if name=="Blower2":
                    pump_status = live_json.get("Blower 2")
                    if pump_status == "OFF":
                        sensor.off()
                    else:
                        sensor.on() # Treat LOW or HIGH as ON
                
                if name == "FilterStatus":
                    sensor.set_state(live_json.get("Filter"))
                
                if name == "OzoneStatus":
                    sensor.set_state(live_json.get("Ozone"))
                
                if name == "HeaterADC":
                    sensor.set_state(live_json.get("Heater ADC"))

                if name == "CurrentADC":
                    sensor.set_state(live_json.get("Current ADC"))

                if name == "Heater1Status":
                    sensor.set_state(live_json.get("Heater 1"))
                
                if name == "Heater2Status":
                    sensor.set_state(live_json.get("Heater 2"))

                # PH/ORP are not updated here, only in the INFO block
            return
            

# --- MODIFIED: This function now calls process_packet ---
def receive(packet):
    """Handle the received packet."""
    process_packet(packet)


# --- MODIFIED: Function signature updated ---
def send_packet_with_debug(spaIP, sensors, connection_sensor):
    global debug
    # Create a TCP client socket
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Connect to the server at IP 192.168.68.106 on port 65534
    client.connect((spaIP, 65534))
  
    # --- ADDED: Set connection sensor to ON ---
    if connection_sensor:
        connection_sensor.on()

    i=0
    #get start time of loop
    start_time = time.time()
    while True:

        # --- MODIFIED COMMAND HANDLING ---
        spacmd = SpaCommand.spa_command() # Create a new command message
        cmd_sent = False # Flag to track if we need to send a packet
        
        try:
            # Check queue for a command
            cmd=producer.cmd_queue.get(timeout=2)
        except queue.Empty:
            cmd=None
        
        if cmd!=None:
            action=cmd["CMD"]
            closeservice=action.get("CloseService")
            newSetpointF=action.get("SetPoint")
            
            # --- Check for all new switch/select commands ---
            pump1_cmd = action.get("pump1")
            pump2_cmd = action.get("pump2")
            pump3_cmd = action.get("pump3")
            lights_cmd = action.get("lights")
            blower1_cmd = action.get("blower1")
            blower2_cmd = action.get("blower2")
            boost_cmd = action.get("boost")
            cl_range_cmd = action.get("cl_range") # <-- ADDED Cl Range

            if closeservice != None:
                #exit SPABoii
                break
            
            if newSetpointF!=None:
                #set new setpoint
                print(f"Setpoint: {newSetpointF}°F")
                
                # HA is now sending F, so we can use it directly
                newSetpointF_int=int(newSetpointF)
                
                spacmd.set_temperature_setpoint_fahrenheit=newSetpointF_int
                cmd_sent = True
            
            if pump1_cmd != None:
                print(f"Pump 1 Command: {pump1_cmd}")
                if pump1_cmd == "OFF":
                    spacmd.set_pump_1 = 0
                elif pump1_cmd == "LOW":
                    spacmd.set_pump_1 = 1
                elif pump1_cmd == "HIGH":
                    spacmd.set_pump_1 = 2
                cmd_sent = True

            if pump2_cmd != None:
                print(f"Pump 2 Command: {pump2_cmd}")
                spacmd.set_pump_2 = 2 if pump2_cmd == "ON" else 0 # Map ON to HIGH (2), OFF to 0
                cmd_sent = True

            if pump3_cmd != None:
                print(f"Pump 3 Command: {pump3_cmd}")
                spacmd.set_pump_3 = 2 if pump3_cmd == "ON" else 0 # Map ON to HIGH (2), OFF to 0
                cmd_sent = True

            if lights_cmd != None:
                print(f"Lights Command: {lights_cmd}")
                spacmd.set_lights = 1 if lights_cmd == "ON" else 0 # Map ON to 1, OFF to 0
                cmd_sent = True

            if blower1_cmd != None:
                print(f"Blower 1 Command: {blower1_cmd}")
                spacmd.set_blower_1 = 2 if blower1_cmd == "ON" else 0 # Map ON to HIGH (2), OFF to 0
                cmd_sent = True

            if blower2_cmd != None:
                print(f"Blower 2 Command: {blower2_cmd}")
                spacmd.set_blower_2 = 2 if blower2_cmd == "ON" else 0 # Map ON to HIGH (2), OFF to 0
                cmd_sent = True
            
            if boost_cmd != None:
                print(f"Boost Command: {boost_cmd}")
                # Set Onzen (SpaBoy) to ON (1) to start boost cycle
                spacmd.set_onzen = 1 
                cmd_sent = True

            # --- ADDED Cl Range LOGIC (WITH CORRECT FIELD NAMES) ---
            if cl_range_cmd != None:
                print(f"Cl Range Command: {cl_range_cmd}")
                if cl_range_cmd == "Low":
                    spacmd.set_orp_level_low = 1195 # This is ID 0x30 (User's ORP Max)
                    spacmd.set_orp_level_high = 1185 # This is ID 0x38 (User's ORP Min)
                elif cl_range_cmd == "Mid":
                    spacmd.set_orp_level_low = 1423
                    spacmd.set_orp_level_high = 1413
                elif cl_range_cmd == "High":
                    spacmd.set_orp_level_low = 1523
                    spacmd.set_orp_level_high = 1513
                cmd_sent = True
            # --- END Cl Range LOGIC ---

            # If any command was added, serialize and send the packet
            if cmd_sent:
                buffer=spacmd.SerializeToString()
                pckt = LevvenPacket(MessageType.COMMAND.value, buffer)
                if debug:
                    print(f"Sending Command Packet Type: {pckt.type}/0x{pckt.type:02X}")
                pack = LevvenToBytes(pckt)
                # Send the serialized packet over the TCP connection
                client.sendall(pack)
                # Adding a small delay after sending a command can be helpful
                time.sleep(0.5)

        # --- END OF MODIFIED COMMAND HANDLING ---


        # Request the configuration on start
        if i  == 0:
            PingSpa(client, MessageType.CONFIGURATION.value)
        # Request the spa information on start after short delay
        elif i  == 4:
            PingSpa(client, MessageType.INFORMATION.value)
        i += 1
        
        # --- MODIFIED PING LOGIC ---
        if i % 4 == 0:
            # Every 4th loop, ping for INFO (which has pH/ORP)
            PingSpa(client, MessageType.INFORMATION.value)
        else:
            # On all other loops, ping for LIVE (which has pumps/temp)
            PingSpa(client, MessageType.LIVE.value)
        # --- END MODIFIED PING LOGIC ---


        temp = bytearray(2048)  # Declare the variable temp
        try:
            temp = client.recv(2048)  # Receive data from the server
            
        except Exception as e:
            #calculate time in minutes since start
            elapsed_minutes = (time.time() - start_time) / 60 /60
            print(f"Connection lost after {elapsed_minutes:.2f} hours")
            
            # --- ADDED: Set connection sensor to OFF ---
            if connection_sensor:
                connection_sensor.off()
            
            client.close()
            
            print("Restarting")
            
            
            #print(f"Error: {e}")
            break

        #print recieved data as hex
        hex_representation = ' '.join(f'{byte:02X}' for byte in temp)

        # Process the received data (which calls receive -> process_packet)
        with io.BytesIO(temp) as net_stream:
            read_and_process_packets(net_stream)
            
        # --- ENTIRE LOGIC BLOCK REMOVED FROM HERE ---
        # All processing is now handled in receive() -> process_packet()
                
    client.close()







# --- MODIFIED: Main loop ---
while True:
    # --- ADDED: Find connection sensor ---
    connection_sensor = find_sensor_by_name(sensors, "ConnectionStatus")
    
    # --- ADDED: Check for spa_ip ---
    if not spa_ip:
        print("Error: spa_ip not found in settings.yaml. Exiting.")
        if connection_sensor:
            connection_sensor.off()
        break
        
    try:
        # --- MODIFIED: Use configured spa_ip ---
        print (f"Spa IP: {spa_ip}")
        # --- MODIFIED: Pass sensor to function ---
        send_packet_with_debug(spa_ip, sensors, connection_sensor)
        # time.sleep(5) # This sleep is problematic, loop will run on connection drop
    except KeyboardInterrupt:
        print("\nCtrl-C detected. Exiting gracefully...")
        # --- ADDED: Set connection sensor to OFF ---
        if connection_sensor:
            connection_sensor.off()
        break  # Exit the loop gracefully
    except Exception as e:
        print(f"An error occurred: {e}. Restarting...")
        # --- ADDED: Set connection sensor to OFF ---
        if connection_sensor:
            connection_sensor.off()
        time.sleep(5) # Prevent rapid-fire restarts on a fatal error
        continue
