#!/usr/bin/env python3
import os
import yaml
import subprocess
import logging
import json
import re
import shlex
from datetime import datetime
from flask import Flask, request, Response  # type: ignore
from prometheus_client import CollectorRegistry, Gauge, generate_latest  # type: ignore


app = Flask("ESXi PERCCLI Exporter")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class PercMetrics:
    def __init__(self, username: str, password: str, host: str) -> None:
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
                    # Check if attr_id is within a reasonable range for SMART attributes (1-255)
                    if not (1 <= attr_id <= 255):
                        i += 1
                        continue
                    
                    # Ensure there are enough bytes for the full 11-byte structure
                    if i + 10 >= len(byte_array):
                        logger.warning(f"Not enough bytes for attribute ID {attr_id} at index {i}. Breaking.")
                        break

                    normalized_value = byte_array[i+3]
                    worst_value = byte_array[i+4]
                    
                    # Raw value is 6 bytes, little-endian
                    raw_value_bytes = byte_array[i+5 : i+11]
                    
                    # Convert 6 little-endian bytes to an integer
                    raw_value = 0
                    for k, byte_val in enumerate(raw_value_bytes):
                        raw_value |= (byte_val << (k * 8))

                    attr_name_map = {
                        0x01: "raw_read_error_rate",
                        0x03: "spin_up_time",
                        0x04: "start_stop_count",
                        0x05: "reallocated_sector_count",
                        0x07: "seek_error_rate",
                        0x09: "power_on_hours",
                        0x0C: "power_cycle_count",
                        0x53: "initial_bad_block_count", # 83 or 'Total_Initial_Bad_Blocks' on some drives
                        0xB1: "wear_leveling_count",     # 177
                        0xB3: "used_reserved_block_count_total", # 179
                        0xB4: "unused_reserved_block_count_total", # 180
                        0xB5: "program_fail_count_total", # 181
                        0xB6: "erase_fail_count_total", # 182
                        0xB7: "runtime_bad_block",      # 183
                        0xB8: "end_to_end_error",       # 184
                        0xBB: "uncorrectable_error_count", # 187
                        0xBE: "airflow_temperature_celsius", # 190
                        0xC2: "temperature_celsius",    # 194
                        0xC3: "hardware_ecc_recovered", # 195
                        0xC5: "current_pending_sector_count", # 197
                        0xC6: "uncorrectable_sector_count", # 198
                        0xC7: "udma_crc_error_count",   # 199
                        0xCA: "data_address_mark_errors", # 202
                        0xEB: "por_recovery_count",     # 235
                        0xF1: "total_host_writes",      # 241
                        0xF2: "total_host_reads",       # 242
                        0xF3: "total_host_writes_expanded", # 243
                        0xF4: "total_host_reads_expanded",  # 244
                        0xF5: "remaining_rated_write_endurance", # 245
                        0xF6: "cumulative_host_sectors_written", # 246
                        0xF7: "host_program_page_count", # 247
                        0xFB: "minimum_spares_remaining", # 251 (or NAND_Writes in smartctl)
                    }
                    
                    attr_name = attr_name_map.get(attr_id, f"unknown_{attr_id:02x}")

                    # Special handle for id 194 (temperature_celsius)
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


    def main(self) -> str:
        logger.info(f"Starting metrics collection for host: {self.host}")
        try:
            data = self.get_perccli_json("/cALL show all J")
        except RuntimeError as e:
            logger.error(f"Failed to fetch initial perccli data: {e}")
            raise

        controllers = data.get("Controllers", [])
        for controller in controllers:
            response = controller.get("Response Data", {})
            controller_index = response.get("Basics", {}).get("Controller", "Unknown")
            self.handle_common_controller(response)
            driver_name = response.get("Version", {}).get("Driver Name", "Unknown")

            if driver_name in ["megaraid_sas", "lsi-mr3"]:
                self.handle_megaraid_controller(response)
            elif driver_name == "mpt3sas":
                self.handle_sas_controller(response)

        return generate_latest(self.registry).decode()

    def handle_common_controller(self, response: dict) -> None:
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        self.metrics["controller_info"].labels(
            controller=controller_index,
            model=response.get("Basics", {}).get("Model", "Unknown"),
            serial=response.get("Basics", {}).get("Serial Number", "Unknown"),
            fwversion=response.get("Version", {}).get("Firmware Version", "Unknown")
        ).set(1)
        status = 1 if response.get("Status", {}).get("Controller Status") == "Optimal" else 0
        self.metrics["controller_status"].labels(controller=controller_index).set(status)
        for key in ["ROC temperature(Degree Celcius)", "ROC temperature(Degree Celsius)"]:
            if key in response.get("HwCfg", {}):
                self.metrics["controller_temperature"].labels(controller=controller_index).set(response["HwCfg"][key])
                break

    def handle_sas_controller(self, response: dict) -> None:
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        self.metrics["controller_status"].labels(controller=controller_index).set(
            1 if response.get("Status", {}).get("Controller Status") == "OK" else 0
        )

    def handle_megaraid_controller(self, response: dict) -> None:
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        for drive in response.get("PD LIST", []):
            enclosure, slot = drive.get("EID:Slt", "0:0").split(":")[:2]
            drive_path = f"/c{controller_index}/e{enclosure}/s{slot}"
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
            except Exception:
                pass

        for attr, value in smart_attributes.items():
            # Ensure value is numeric before setting
            if isinstance(value, (int, float)):
                self.metrics["drive_smart"].labels(controller=controller_index, drive=drive_identifier, attribute=attr).set(value)
            else:
                logger.warning(f"SMART attribute '{attr}' for drive '{drive_identifier}' has non-numeric value '{value}'. Skipping.")

    def get_perccli_json(self, perccli_args: str) -> dict:
        return self._run_perccli_command(perccli_args)

    def get_perccli_smart(self, drive_path: str) -> str:
        cmd = f"{drive_path} show smart"
        output = self._run_perccli_command(cmd, expect_json=False)

        match = re.search(r'Smart Data Info .*? = \n\s*([0-9a-fA-F\s\n]+?)(?:\n\n|\Z)', output, re.DOTALL)
        
        if match:
            cleaned_hex_data = re.sub(r'\s+', '', match.group(1)).strip()
            return cleaned_hex_data
        else:
            logger.warning(f"No SMART data found in perccli output for {drive_path}")
            return ""

    def _run_perccli_command(self, perccli_args: str, expect_json: bool = True) -> dict | str:
        safe_args = shlex.quote(perccli_args)
        cmd = f"ssh -o StrictHostKeyChecking=no {shlex.quote(self.username)}@{shlex.quote(self.host)} 'perccli64 {safe_args}'"
        logger.debug(f"Executing command: {cmd}")
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            stdout, stderr = proc.communicate(timeout=60)

            if proc.returncode != 0:
                error_msg = f"perccli command '{perccli_args}' failed with exit code {proc.returncode}. Stderr: {stderr.strip()}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            if expect_json:
                try:
                    return json.loads(stdout)
                except json.JSONDecodeError as e:
                    error_msg = f"Failed to decode JSON from perccli output. Error: {e}. Output: {stdout.strip()}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            error_msg = f"perccli command '{perccli_args}' timed out after 60 seconds. Stderr: {stderr.decode().strip()}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"An error occurred while running perccli command '{perccli_args}': {e}. Stderr: {stderr.decode().strip() if 'stderr' in locals() else ''}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

@app.route("/metrics")
def metrics_route():
    target = request.args.get("target")
    if not target or target not in config["targets"]:
        return Response(f"Invalid target '{target}'", status=400, mimetype="text/plain")
    tcfg = config["targets"][target]
    
    metrics_collector = PercMetrics(tcfg["username"], tcfg["password"], target)
    try:
        metrics = metrics_collector.main()
        return Response(metrics, mimetype="text/plain; version=0.0.4")
    except RuntimeError as e:
        logger.error(f"Error collecting metrics for target {target}: {e}")
        return Response(f"Error collecting metrics: {e}", status=500, mimetype="text/plain")


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found at {path}")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML configuration from {path}: {e}")
        raise

if __name__ == "__main__":
    config_path = os.environ.get("CONFIG_FILE_PATH", "config.yml")
    config = load_config(config_path)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10424)), debug=True)