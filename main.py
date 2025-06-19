#!/usr/bin/env python3
import os
import yaml
import subprocess
import logging
import json
from datetime import datetime
from flask import Flask, request, Response # type: ignore
from prometheus_client import CollectorRegistry, Gauge, generate_latest # type: ignore
import shlex
import re

app = Flask("ESXi PERCCLI Exporter")

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class PercMetrics:
    """Class to collect and expose PERCCLI metrics, including SMART data, for Prometheus."""
    def __init__(self, username: str, password: str, host: str) -> None:
        """Initialize PercMetrics with credentials and host."""
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
        """Parse SMART data from hexadecimal string."""
        logger.debug(f"Parsing SMART data hex: {smart_data_hex}")
        try:
            hex_clean = re.sub(r'[^0-9a-fA-F]', '', smart_data_hex)
            bytes_list = [hex_clean[i:i+2] for i in range(0, len(hex_clean), 2)]
            attributes = {}

            i = 0
            while i < len(bytes_list):
                if i + 12 > len(bytes_list):
                    break

                attr_id_candidate = int(bytes_list[i], 16)
                if attr_id_candidate == 0x00:
                    i += 1
                    continue
                
                current_block = bytes_list[i:i+12]
                
                attr_id = int(current_block[0], 16)

                raw_value_bytes = current_block[5:11]
                raw_value_bytes_reversed = raw_value_bytes[::-1] # Little-endian
                raw_value_hex = ''.join(raw_value_bytes_reversed)
                
                try:
                    raw_value = int(raw_value_hex, 16)
                except ValueError:
                    logger.warning(f"Could not convert raw value hex '{raw_value_hex}' for attribute ID {attr_id}. Skipping this potential block by 1 byte.")
                    i += 1
                    continue

                attr_name_map = {
                    0x01: "raw_read_error_rate",
                    0x03: "spin_up_time",
                    0x04: "start_stop_count",
                    0x05: "reallocated_sector_count",
                    0x07: "seek_error_rate",
                    0x09: "power_on_hours",
                    0x0C: "power_cycle_count",
                    0x53: "initial_bad_block_count", # Added based on documentation for 0x53
                    0xB1: "wear_leveling_count", # Documentation lists B1 as "Wear Range Delta", but your log suggests it's similar to wear leveling count. Will keep your name for now.
                    0xB3: "used_reserved_block_count_total",
                    0xB5: "program_fail_count_total",
                    0xB6: "erase_fail_count_total",
                    0xB7: "runtime_bad_block", # Also "SATA Downshift Error Count" - kept your existing name
                    0xBB: "uncorrectable_error_count", # Doc says "Reported Uncorrectable Errors"
                    0xBE: "airflow_temperature_celsius", # Doc says "Temperature Difference or Airflow Temperature"
                    0xC2: "temperature_celsius", # Doc says "Temperature or Temperature Celsius"
                    0xC3: "hardware_ecc_recovered",
                    0xC4: "reallocation_event_count",
                    0xC6: "uncorrectable_sector_count", # Doc says "(Offline) Uncorrectable Sector Count"
                    0xC7: "udma_crc_error_count", # Doc says "UltraDMA CRC Error Count"
                    0xEB: "por_recovery_count", # Doc says "Good Block Count AND System(Free) Block Count" for 0xEB, which is different than your name. Given your name and the value, it might be specific to your drive. I will keep your name based on observed value logic.
                    0xF1: "total_host_writes", # Doc says "Total LBAs Written or Total Host Writes"
                    0xF2: "total_host_reads", # Added, common to have alongside F1
                    0xE6: "g_sense_error_rate", # Also documented as 0xBF
                    0xE7: "ssd_life_left", # Doc says "Life Left (SSDs) or Temperature"
                }
                
                attr_name = attr_name_map.get(attr_id, f"unknown_{attr_id}")
                
                # Special handling for temperature attributes as per documentation and common practice
                # For 0xBE (Airflow Temperature), the doc suggests it's often (100 - temp. °C) or raw temp.
                # Given your raw value was '1a' (26) which is plausible for temperature:
                # We'll take current_block[5] (first byte of raw value) as the temperature.
                if attr_id == 0xBE:
                    temp_val_byte = current_block[5] # First byte of the raw value (bytes 5-10)
                    try:
                        raw_value = int(temp_val_byte, 16)
                    except ValueError:
                        logger.warning(f"Could not convert temperature byte '{temp_val_byte}' for attribute ID {attr_id}. Using full raw value as fallback.")
                        # If conversion fails, the raw_value from the full 6 bytes will be used
                        
                # For 0xC2 (Temperature Celsius), the doc explicitly states "Lowest byte of the raw value contains the exact temperature value (Celsius degrees)."
                if attr_id == 0xC2:
                    # The lowest byte of the 6-byte raw value (current_block[10])
                    temp_val_byte = current_block[10]
                    try:
                        raw_value = int(temp_val_byte, 16)
                    except ValueError:
                        logger.warning(f"Could not convert temperature byte '{temp_val_byte}' for attribute ID {attr_id}. Using full raw value as fallback.")
                        pass # Keep the default raw_value from bytes 5-10
                
                attributes[attr_name] = raw_value
                i += 12 # Move to the next 12-byte block
                
        except Exception as e:
            logger.error(f"Failed to parse SMART data: {e}", exc_info=True)
            return {}
            
        logger.debug(f"Parsed SMART attributes: {attributes}")
        return attributes


    def main(self) -> str:
        """Main method to collect and generate Prometheus metrics."""
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
        """Handle common controller metrics."""
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
        """Handle SAS controller metrics."""
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")
        self.metrics["controller_status"].labels(controller=controller_index).set(
            1 if response.get("Status", {}).get("Controller Status") == "OK" else 0
        )

    def handle_megaraid_controller(self, response: dict) -> None:
        """Handle MegaRAID controller metrics, including SMART data."""
        controller_index = response.get("Basics", {}).get("Controller", "Unknown")

        for drive in response.get("PD LIST", []):
            enclosure, slot = drive.get("EID:Slt", "0:0").split(":")[:2]
            drive_path = f"/c{controller_index}/e{enclosure}/s{slot}"
            smart_data = self.get_perccli_smart(drive_path)
            smart_attributes = self.parse_smart_data(smart_data)
            logger.debug(f"SMART ATTRIBUTES: {smart_attributes}")
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
        """Create metrics for a physical drive, including SMART attributes."""
        enclosure, slot = physical_drive.get("EID:Slt", "0:0").split(":")[:2]
        drive_identifier = f"Drive /c{controller_index}/e{enclosure}/s{slot}"
        state = physical_drive.get("State", "Unknown")
        status = 1 if state == "Onln" else 0
        self.metrics["drive_status"].labels(controller=controller_index, drive=drive_identifier).set(status)

        if "Temp" in physical_drive:
            temp = int(physical_drive["Temp"].replace("C", ""))
            self.metrics["drive_temp"].labels(controller=controller_index, drive=drive_identifier).set(temp)
        for attr, value in smart_attributes.items():

            self.metrics["drive_smart"].labels(controller=controller_index, drive=drive_identifier, attribute=attr).set(value)


    def get_perccli_json(self, perccli_args: str) -> dict:
        """Execute perccli command over SSH and return JSON output."""
        return self._run_perccli_command(perccli_args)


    def get_perccli_smart(self, drive_path: str) -> str:
        """Fetch SMART data for a specific drive."""
        cmd = f"{drive_path} show smart"
        output = self._run_perccli_command(cmd, expect_json=False)
        match = re.search(r'Smart Data Info .*? = \n([0-9a-fA-F \n]+)', output, re.DOTALL)
        return match.group(1).replace('\n', '').strip() if match else ""


    def _run_perccli_command(self, perccli_args: str, expect_json: bool = True) -> dict | str:
        """Internal method to run perccli command over SSH."""
        safe_args = shlex.quote(perccli_args)
        cmd = f"sshpass -p {shlex.quote(self.password)} ssh -o StrictHostKeyChecking=no {shlex.quote(self.username)}@{shlex.quote(self.host)} 'cd /opt/lsi/perccli && ./perccli {safe_args}'"
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"perccli failed: {stderr}")
        if expect_json:
            return json.loads(stdout)
        return stdout


@app.route("/metrics")
def metrics_route():
    """Handle /metrics endpoint to serve Prometheus metrics."""
    target = request.args.get("target")
    if not target or target not in config["targets"]:
        return Response(f"Invalid target '{target}'", status=400, mimetype="text/plain")
    tcfg = config["targets"][target]
    metrics = PercMetrics(tcfg["username"], tcfg["password"], target).main()
    return Response(metrics, mimetype="text/plain; version=0.0.4")


def load_config(path: str) -> dict:
    """Load configuration from YAML file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    config_path = os.environ.get("CONFIG_FILE_PATH", "/app/config.yml")
    config = load_config(config_path)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10424)), debug=True)