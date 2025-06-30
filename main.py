#!/usr/bin/env python3
import os
import yaml # type: ignore
import subprocess
import logging
import json
import shlex
import re
from datetime import datetime
from flask import Flask, request, Response # type: ignore
from prometheus_client import CollectorRegistry, Gauge, generate_latest # type: ignore

app = Flask("ESXi PERCCLI Exporter")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class PercMetrics:
    def __init__(self, username: str, password: str, host: str) -> None:
        logger.debug(f"Initializing PercMetrics for host: {host}")
        self.registry = CollectorRegistry()
        self.namespace = "megaraid"
        self.username = username
        self.password = password
        self.host = host
        self.metrics = {
            "controller_info": Gauge(
                f"{self.namespace}_controller_info",
                "MegaRAID controller info",
                ["controller", "model", "serial", "fwversion"],
                registry=self.registry
            ),
            "controller_status": Gauge(
                f"{self.namespace}_controller_status",
                "Controller status (1=Optimal, 0=Not Optimal)",
                ["controller"],
                registry=self.registry
            ),
            "controller_temperature": Gauge(
                f"{self.namespace}_controller_temperature",
                "Controller temperature in Celsius",
                ["controller"],
                registry=self.registry
            ),
            "drive_status": Gauge(
                f"{self.namespace}_drive_status",
                "Physical drive status (1=Online, 0=Other)",
                ["controller", "drive"],
                registry=self.registry
            ),
            "drive_temp": Gauge(
                f"{self.namespace}_drive_temp",
                "Physical drive temperature in Celsius",
                ["controller", "drive"],
                registry=self.registry
            ),
            "drive_smart": Gauge(
                f"{self.namespace}_drive_smart",
                "Drive SMART attributes",
                ["controller", "drive", "attribute"],
                registry=self.registry
            ),
            "virtual_drive_status": Gauge(
                f"{self.namespace}_virtual_drive_status",
                "Virtual drive status (1=Optimal, 0=Other)",
                ["controller", "vd"],
                registry=self.registry
            ),
            "bbu_health": Gauge(
                f"{self.namespace}_bbu_health",
                "Battery Backup Unit health (1=Healthy, 0=Unhealthy)",
                ["controller"],
                registry=self.registry
            ),
        }

    def parse_smart_data(self, smart_data_hex: str) -> dict:
        attributes = {}
        try:
            hex_clean = re.sub(r'[^0-9a-fA-F]', '', smart_data_hex)
            byte_array = []
            for i in range(0, len(hex_clean), 2):
                byte_array.append(int(hex_clean[i:i+2], 16))
            
            start_index = 0
            if len(byte_array) >= 2 and (byte_array[0] == 0x01 and byte_array[1] == 0x00) or (byte_array[0] == 0x2f and byte_array[1] == 0x00):
                start_index = 2

            i = start_index
            while i + 10 < len(byte_array):
                try:
                    attr_id = byte_array[i]
                    if not (1 <= attr_id <= 255):
                        i += 1
                        continue
                    
                    if i + 10 >= len(byte_array):
                        logger.warning(f"Not enough bytes for attribute ID {attr_id} at index {i}. Breaking.")
                        break

                    normalized_value = byte_array[i+3]
                    worst_value = byte_array[i+4]
                    raw_value_bytes = byte_array[i+5 : i+11]
                    raw_value = 0
                    for k, byte_val in enumerate(raw_value_bytes):
                        raw_value |= (byte_val << (k * 8))

                    attr_name_map = {
                        0x01: "raw_read_error_rate", 0x03: "spin_up_time", 0x04: "start_stop_count", 0x05: "reallocated_sector_count", 
                        0x07: "seek_error_rate", 0x09: "power_on_hours", 0x0C: "power_cycle_count", 0x53: "initial_bad_block_count", 
                        0xB1: "wear_leveling_count", 0xB3: "used_reserved_block_count_total", 0xB4: "unused_reserved_block_count_total", 
                        0xB5: "program_fail_count_total", 0xB6: "erase_fail_count_total", 0xB7: "runtime_bad_block", 0xB8: "end_to_end_error",
                        0xBB: "uncorrectable_error_count", 0xBE: "airflow_temperature_celsius", 0xC2: "temperature_celsius", 0xC3: "hardware_ecc_recovered",
                        0xC5: "current_pending_sector_count", 0xC6: "uncorrectable_sector_count", 0xC7: "udma_crc_error_count", 0xCA: "data_address_mark_errors", 
                        0xEB: "por_recovery_count", 0xF1: "total_host_writes", 0xF2: "total_host_reads", 0xF3: "total_host_writes_expanded", 
                        0xF4: "total_host_reads_expanded", 0xF5: "remaining_rated_write_endurance", 0xF6: "cumulative_host_sectors_written", 
                        0xF7: "host_program_page_count", 0xFB: "minimum_spares_remaining",
                    }
                    
                    attr_name = attr_name_map.get(attr_id, f"unknown_{attr_id:02x}")
                    attributes[attr_name] = raw_value_bytes[0] if attr_id == 0xC2 else raw_value
                    i += 11
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse 11-byte block at index {i}: {e}. Trying next byte.")
                    i += 1
        except Exception as e:
            logger.error(f"Failed to process SMART data string: {e}", exc_info=True)
            return {}
        logger.debug(f"Parsed SMART attributes: {attributes}")
        return attributes

    def parse_smartctl_nvme(self, smartctl_cmd: str) -> tuple[dict, str]:
        logger.debug(f"Entering parse_smartctl_nvme with command: {smartctl_cmd}")
        try:
            out = self.run_remote_cmd(smartctl_cmd)
            logger.debug(f"Raw smartctl output: {out[:500]}...")
            data = json.loads(out)
            attrs = {}
            serial = data.get("serial_number", "unknown")
            smart_log = data.get("nvme_smart_health_information_log", {})
            
            # Map SMART attributes to metric-friendly names
            attribute_map = {
                "critical_warning": "critical_warning",
                "temperature": "temperature_celsius",
                "available_spare": "available_spare_percent",
                "available_spare_threshold": "available_spare_threshold_percent",
                "percentage_used": "percentage_used",
                "data_units_read": "data_units_read",
                "data_units_written": "data_units_written",
                "host_reads": "host_reads",
                "host_writes": "host_writes",
                "controller_busy_time": "controller_busy_time_minutes",
                "power_cycles": "power_cycles",
                "power_on_hours": "power_on_hours",
                "unsafe_shutdowns": "unsafe_shutdowns",
                "media_errors": "media_errors",
                "num_err_log_entries": "error_log_entries",
                "warning_temp_time": "warning_temperature_time_minutes",
                "critical_comp_time": "critical_composite_temperature_time_minutes"
            }
            
            for json_key, metric_name in attribute_map.items():
                if json_key in smart_log:
                    value = smart_log[json_key]
                    if json_key in ["data_units_read", "data_units_written"]:
                        value = value * 512 * 1000  # Convert to bytes
                    if json_key == "percentage_used":
                        metric_name = "ssd_life_left"
                        value = 100 - value
                    attrs[metric_name] = value
            
            # Handle temperature_sensors array
            if "temperature_sensors" in smart_log:
                for i, temp in enumerate(smart_log["temperature_sensors"]):
                    attrs[f"temperature_sensor_{i+1}_celsius"] = temp

            # Add additional attributes
            if "nvme_total_capacity" in data:
                attrs["total_capacity_bytes"] = data["nvme_total_capacity"]
            if "smart_status" in data and "passed" in data["smart_status"]:
                attrs["smart_status_passed"] = 1 if data["smart_status"]["passed"] else 0

        except Exception as e:
            logger.error(f"Error parsing NVMe smartctl output: {e}\nRaw output: {out[:1000]}", exc_info=True)
            return {}, "unknown"
        return attrs, serial

    def parse_smartctl_scsi(self, smartctl_cmd: str) -> tuple[dict, str]:
        logger.debug(f"Entering parse_smartctl_scsi with command: {smartctl_cmd}")
        try:
            out = self.run_remote_cmd(smartctl_cmd)
            logger.debug(f"Raw smartctl output: {out[:500]}...")
            data = json.loads(out)
            attrs = {}
            serial = data.get("serial_number", "unknown")

            # Temperature
            if "temperature" in data:
                attrs["temperature_celsius"] = data["temperature"]["current"]

            # Power-On Time
            if "power_on_time" in data:
                attrs["power_on_hours"] = data["power_on_time"]["hours"]

            # Start-Stop and Load-Unload Cycles
            if "scsi_start_stop_cycle_counter" in data:
                cycle = data["scsi_start_stop_cycle_counter"]
                attrs["specified_cycle_count_over_device_lifetime"] = cycle.get("specified_cycle_count_over_device_lifetime", 0)
                attrs["accumulated_start_stop_cycles"] = cycle.get("accumulated_start_stop_cycles", 0)
                attrs["specified_load_unload_count_over_device_lifetime"] = cycle.get("specified_load_unload_count_over_device_lifetime", 0)
                attrs["accumulated_load_unload_cycles"] = cycle.get("accumulated_load_unload_cycles", 0)

            # Grown Defect List
            if "scsi_grown_defect_list" in data:
                attrs["grown_defect_list"] = data["scsi_grown_defect_list"]

            # Error Counter Log
            if "scsi_error_counter_log" in data:
                error_log = data["scsi_error_counter_log"]
                for op in ["read", "write", "verify"]:
                    if op in error_log:
                        op_data = error_log[op]
                        attrs[f"{op}_errors_corrected_by_eccfast"] = op_data.get("errors_corrected_by_eccfast", 0)
                        attrs[f"{op}_errors_corrected_by_eccdelayed"] = op_data.get("errors_corrected_by_eccdelayed", 0)
                        attrs[f"{op}_errors_corrected_by_rereads_rewrites"] = op_data.get("errors_corrected_by_rereads_rewrites", 0)
                        attrs[f"{op}_total_errors_corrected"] = op_data.get("total_errors_corrected", 0)
                        attrs[f"{op}_correction_algorithm_invocations"] = op_data.get("correction_algorithm_invocations", 0)
                        attrs[f"{op}_gigabytes_processed"] = float(op_data.get("gigabytes_processed", "0"))
                        attrs[f"{op}_total_uncorrected_errors"] = op_data.get("total_uncorrected_errors", 0)

            # Pending Defects
            if "scsi_pending_defects" in data:
                attrs["pending_defects_count"] = data["scsi_pending_defects"]["count"]

            # Self-Test Results
            if "scsi_self_test_0" in data:
                test_0 = data["scsi_self_test_0"]
                attrs["self_test_0_result"] = test_0["result"]["value"]
                attrs["self_test_0_power_on_time"] = test_0["power_on_time"]["hours"]

            if "scsi_self_test_1" in data:
                test_1 = data["scsi_self_test_1"]
                attrs["self_test_1_result"] = test_1["result"]["value"]
                attrs["self_test_1_power_on_time"] = test_1["power_on_time"]["hours"]

            # Extended Self-Test Duration
            if "scsi_extended_self_test_seconds" in data:
                attrs["extended_self_test_seconds"] = data["scsi_extended_self_test_seconds"]

            # SMART Status
            if "smart_status" in data:
                attrs["smart_status_passed"] = 1 if data["smart_status"]["passed"] else 0

            logger.debug(f"Parsed SCSI SMART data: {attrs}, Serial: {serial}")
        except Exception as e:
            logger.error(f"Error parsing SCSI smartctl output: {e}\nRaw output: {out[:1000]}", exc_info=True)
            return {}, "unknown"
        return attrs, serial

    def discover_scsi_nvme_devices(self) -> list[tuple[str, str, str, str]]:
        logger.debug("Entering discover_scsi_nvme_devices.")
        detected = []
        try:
            out = self.run_remote_cmd("smartctl --scan-open")
            logger.debug(f"smartctl --scan-open output: {out.strip()}")
            for line in out.strip().splitlines():
                if line.startswith("#") or "open failed" in line.lower():
                    logger.debug(f"Skipping line: '{line}'")
                    continue
                match = re.match(r'(/dev/\S+)\s+(-d\s+\S+(?:,\d+)?)\s*(?:#.*?\[(\S+)\])?', line)
                if match:
                    dev_path, d_arg, disk_id = match.groups()
                    disk_id = disk_id or d_arg.replace("-d ", "").replace(",", "_")
                    smartctl_cmd = f"smartctl -a -j {dev_path} {d_arg}"
                    if "-d nvme" in d_arg.lower():
                        detected.append(("nvme", dev_path, smartctl_cmd, disk_id))
                    elif "-d scsi" in d_arg.lower() or "-d megaraid" in d_arg.lower() or "-d sat+megaraid" in d_arg.lower():
                        detected.append(("scsi", dev_path, smartctl_cmd, disk_id))
                    else:
                        logger.debug(f"Skipping unhandled device: {dev_path} with -d '{d_arg}'")
        except Exception as e:
            logger.error(f"Error discovering devices: {e}", exc_info=True)
        logger.debug(f"Discovered devices: {detected}")
        return detected

    def main(self) -> str:
        try:
            data = self.get_perccli_json("/cALL show all J")
        except RuntimeError as e:
            logger.error(f"Failed to get perccli data: {e}")
            raise
        controllers = data.get("Controllers", [])
        logger.debug(f"Found {len(controllers)} controllers.")
        perccli_devices = set()
        for controller in controllers:
            response = controller.get("Response Data", {})
            controller_index = response.get("Basics", {}).get("Controller", "Unknown")
            logger.debug(f"Processing controller index: {controller_index}")
            self.handle_common_controller(response)
            driver_name = response.get("Version", {}).get("Driver Name", "Unknown")
            logger.debug(f"Controller {controller_index} driver name: {driver_name}")
            if driver_name in ["megaraid_sas", "lsi-mr3"]:
                self.handle_megaraid_controller(response)
                for pd in response.get("PD LIST", []):
                    enclosure, slot = pd.get("EID:Slt", "0:0").split(":")[:2]
                    perccli_devices.add(f"/c{controller_index}/e{enclosure}/s{slot}")
                    logger.debug(f"Added perccli device to set: /c{controller_index}/e{enclosure}/s{slot}")
        logger.debug("Discovering extra SCSI/NVMe devices.")
        extra_devs = self.discover_scsi_nvme_devices()
        for dtype, dev_path, smartctl_cmd, disk_id in extra_devs:
            logger.debug(f"Processing extra device: {dev_path} of type {dtype} with smartctl cmd: '{smartctl_cmd}' and ID: {disk_id}")
            if dtype == "nvme":
                attrs, serial = self.parse_smartctl_nvme(smartctl_cmd)
            elif dtype == "scsi":
                attrs, serial = self.parse_smartctl_scsi(smartctl_cmd)
            else:
                logger.warning(f"Skipping unhandled device type: {dtype} for {dev_path}")
                continue
            drive_label = disk_id if dtype == "scsi" else dev_path
            for k, v in attrs.items():
                self.metrics["drive_smart"].labels(controller="none", drive=drive_label, attribute=k).set(v)
        latest_metrics = generate_latest(self.registry).decode()
        return latest_metrics

    def handle_common_controller(self, response: dict) -> None:
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        model = response.get("Basics", {}).get("Model", "Unknown")
        serial = response.get("Basics", {}).get("Serial Number", "Unknown")
        fwversion = response.get("Version", {}).get("Firmware Version", "Unknown")
        self.metrics["controller_info"].labels(
            controller=controller_index,
            model=model,
            serial=serial,
            fwversion=fwversion
        ).set(1)
        status = 1 if response.get("Status", {}).get("Controller Status") == "Optimal" else 0
        self.metrics["controller_status"].labels(controller=controller_index).set(status)
        for key in ["ROC temperature(Degree Celcius)", "ROC temperature(Degree Celsius)"]:
            if key in response.get("HwCfg", {}):
                temp = response["HwCfg"][key]
                self.metrics["controller_temperature"].labels(controller=controller_index).set(temp)
                break

    def handle_megaraid_controller(self, response: dict) -> None:
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        for drive in response.get("PD LIST", []):
            enclosure, slot = drive.get("EID:Slt", "0:0").split(":")[:2]
            drive_path = f"/c{controller_index}/e{enclosure}/s{slot}"
            logger.debug(f"Processing physical drive: {drive_path}")
            smart_data = self.get_perccli_smart(drive_path)
            smart_attributes = self.parse_smart_data(smart_data)
            self.create_metrics_of_physical_drive(drive, [], controller_index, smart_attributes)
        for vd in response.get("VD LIST", []):
            vd_position = vd.get("DG/VD", "0/0")
            drive_group, volume_group = vd_position.split("/")[:2]
            vd_id = f"DG{drive_group}/VD{volume_group}"
            status = 1 if vd.get("State", "Unknown") == "Optl" else 0
            self.metrics["virtual_drive_status"].labels(controller=controller_index, vd=vd_id).set(status)
        bbu_status = response.get("Status", {}).get("BBU Status", "NA")
        if bbu_status != "NA":
            bbu_health = 1 if bbu_status in [0, 8, 4096] else 0
            self.metrics["bbu_health"].labels(controller=controller_index).set(bbu_health)

    def create_metrics_of_physical_drive(self, physical_drive: dict, detailed_info_array: list, controller_index: str, smart_attributes: dict) -> None:
        enclosure, slot = physical_drive.get("EID:Slt", "0:0").split(":")[:2]
        drive_identifier = f"Drive /c{controller_index}/e{enclosure}/s{slot}"
        state = physical_drive.get("State", "Unknown")
        status = 1 if state == "Onln" else 0
        self.metrics["drive_status"].labels(controller=controller_index, drive=drive_identifier).set(status)
        if "Temp" in physical_drive:
            try:
                temp = int(physical_drive["Temp"].replace("C", ""))
                self.metrics["drive_temp"].labels(controller=controller_index, drive=drive_identifier).set(temp)
            except ValueError:
                logger.warning(f"Could not parse temperature for {drive_identifier}: {physical_drive['Temp']}")
        for attr, value in smart_attributes.items():
            self.metrics["drive_smart"].labels(controller=controller_index, drive=drive_identifier, attribute=attr).set(value)

    def get_perccli_json(self, perccli_args: str) -> dict:
        logger.debug(f"Entering get_perccli_json with args: {perccli_args}")
        result = self._run_perccli_command(perccli_args)
        return result

    def get_perccli_smart(self, drive_path: str) -> str:
        logger.debug(f"Entering get_perccli_smart for drive: {drive_path}")
        cmd = f"{drive_path} show smart"
        output = self._run_perccli_command(cmd, expect_json=False)
        match = re.search(r'Smart Data Info .*? = \n([0-9a-fA-F \n]+)', output, re.DOTALL)
        smart_data = match.group(1).replace('\n', '').strip() if match else ""
        if not smart_data:
            logger.warning(f"No SMART data found for {drive_path}")
        else:
            logger.debug(f"Extracted SMART data length for {drive_path}: {len(smart_data)}")
        return smart_data

    def _run_perccli_command(self, perccli_args: str, expect_json: bool = True) -> dict | str:
        logger.debug(f"Entering _run_perccli_command with args: {perccli_args}, expect_json: {expect_json}")
        safe_args = shlex.quote(perccli_args)
        cmd = f"ssh -i /root/.ssh/id_rsa_exporter -p 22 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {shlex.quote(self.username)}@{shlex.quote(self.host)} 'cd /opt/lsi/perccli/ && ./perccli64 {safe_args}'"
        logger.debug(f"Executing command: {cmd}")
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                logger.error(f"perccli command failed with return code {proc.returncode}: {stderr}")
                raise RuntimeError(f"perccli failed: {stderr}")
            if expect_json:
                try:
                    parsed_json = json.loads(stdout)
                    return parsed_json
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode JSON from perccli output: {e}. Output: {stdout[:500]}...")
                    raise RuntimeError(f"Invalid JSON output from perccli: {e}")
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            logger.error(f"perccli command timed out after 30 seconds. Stderr: {stderr}")
            raise RuntimeError(f"perccli command timed out: {stderr}")
        except Exception as e:
            logger.error(f"Error executing perccli command: {e}", exc_info=True)
            raise

    def run_remote_cmd(self, command: str) -> str:
        logger.debug(f"Entering run_remote_cmd with command: {command}")
        cmd = f"ssh -i /root/.ssh/id_rsa_exporter -p 22 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@103.90.225.9 {shlex.quote(command)}"
        logger.debug(f"Executing remote command: {cmd}")
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                logger.error(f"SSH command failed with return code {proc.returncode}: {stderr}")
                raise RuntimeError(f"SSH command failed: {stderr}")
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            logger.error(f"SSH command timed out after 30 seconds. Stderr: {stderr}")
            raise RuntimeError(f"SSH command timed out: {stderr}")
        except Exception as e:
            logger.error(f"Error executing remote command: {e}", exc_info=True)
            raise

@app.route("/metrics")
def metrics_route():
    logger.debug("Received request for /metrics.")
    target = request.args.get("target")
    if not target:
        logger.warning("No target specified in /metrics request.")
        return Response("Parameter 'target' is missing.", status=400, mimetype="text/plain")
    if target not in config["targets"]:
        logger.warning(f"Invalid target '{target}' requested.")
        return Response(f"Invalid target '{target}'", status=400, mimetype="text/plain")
    
    tcfg = config["targets"][target]
    logger.debug(f"Processing metrics for target: {target}")
    try:
        metrics_collector = PercMetrics(tcfg["username"], tcfg["password"], target)
        metrics = metrics_collector.main()
        logger.debug(f"Successfully generated metrics for target {target}.")
        return Response(metrics, mimetype="text/plain; version=0.0.4")
    except RuntimeError as e:
        logger.error(f"Error generating metrics for target {target}: {e}")
        return Response(f"Error generating metrics for target {target}: {e}", status=500, mimetype="text/plain")
    except Exception as e:
        logger.critical(f"An unhandled error occurred while generating metrics for target {target}: {e}", exc_info=True)
        return Response(f"An internal error occurred: {e}", status=500, mimetype="text/plain")

def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            cfg = yaml.safe_load(f)
            return cfg
    except FileNotFoundError:
        logger.critical(f"Configuration file not found at {path}")
        raise
    except yaml.YAMLError as e:
        logger.critical(f"Error parsing YAML configuration file: {e}")
        raise

if __name__ == "__main__":
    config_path = os.environ.get("CONFIG_FILE_PATH", "config.yml")
    try:
        config = load_config(config_path)
    except Exception as e:
        logger.critical(f"Failed to load configuration, exiting: {e}")
        exit(1)
    
    port = int(os.environ.get("PORT", 10424))
    logger.debug(f"Application configured to run on host 0.0.0.0 and port {port}.")
    app.run(host="0.0.0.0", port=port, debug=True)