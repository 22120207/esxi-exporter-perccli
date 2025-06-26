# (Remote) ESXi PERCCLI Exporter

```
root@agent:/home/esxi-perccli-exporter# curl http://localhost:10424/metrics?target=10.0.100.252
# HELP megaraid_controller_info MegaRAID controller info
# TYPE megaraid_controller_info gauge
megaraid_controller_info{controller="0",fwversion="4.300.00-8368",model="PERC H730 Mini",serial="79L013X"} 1.0
# HELP megaraid_controller_status Controller status (1=Optimal, 0=Not Optimal)
# TYPE megaraid_controller_status gauge
megaraid_controller_status{controller="0"} 1.0
# HELP megaraid_controller_temperature Controller temperature in Celsius
# TYPE megaraid_controller_temperature gauge
megaraid_controller_temperature{controller="0"} 56.0
# HELP megaraid_drive_status Physical drive status (1=Online, 0=Other)
# TYPE megaraid_drive_status gauge
megaraid_drive_status{controller="0",drive="Drive /c0/e32/s2"} 1.0
megaraid_drive_status{controller="0",drive="Drive /c0/e32/s3"} 1.0
# HELP megaraid_drive_temp Physical drive temperature in Celsius
# TYPE megaraid_drive_temp gauge
# HELP megaraid_drive_smart Drive SMART attributes
# TYPE megaraid_drive_smart gauge
megaraid_drive_smart{attribute="raw_read_error_rate",controller="0",drive="Drive /c0/e32/s2"} 25700.0
megaraid_drive_smart{attribute="power_on_hours",controller="0",drive="Drive /c0/e32/s2"} 29618.0
megaraid_drive_smart{attribute="power_cycle_count",controller="0",drive="Drive /c0/e32/s2"} 20.0
megaraid_drive_smart{attribute="wear_leveling_count",controller="0",drive="Drive /c0/e32/s2"} 100.0
megaraid_drive_smart{attribute="used_reserved_block_count_total",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="program_fail_count_total",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="erase_fail_count_total",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="runtime_bad_block",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="uncorrectable_error_count",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="airflow_temperature_celsius",controller="0",drive="Drive /c0/e32/s2"} 26.0
megaraid_drive_smart{attribute="hardware_ecc_recovered",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="udma_crc_error_count",controller="0",drive="Drive /c0/e32/s2"} 0.0
megaraid_drive_smart{attribute="por_recovery_count",controller="0",drive="Drive /c0/e32/s2"} 14.0
megaraid_drive_smart{attribute="total_host_writes",controller="0",drive="Drive /c0/e32/s2"} 2.20315629552e+011
megaraid_drive_smart{attribute="initial_bad_block_count",controller="0",drive="Drive /c0/e32/s2"} 40962.0
megaraid_drive_smart{attribute="raw_read_error_rate",controller="0",drive="Drive /c0/e32/s3"} 25700.0
megaraid_drive_smart{attribute="power_on_hours",controller="0",drive="Drive /c0/e32/s3"} 29622.0
megaraid_drive_smart{attribute="power_cycle_count",controller="0",drive="Drive /c0/e32/s3"} 22.0
megaraid_drive_smart{attribute="wear_leveling_count",controller="0",drive="Drive /c0/e32/s3"} 97.0
megaraid_drive_smart{attribute="used_reserved_block_count_total",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="program_fail_count_total",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="erase_fail_count_total",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="runtime_bad_block",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="uncorrectable_error_count",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="airflow_temperature_celsius",controller="0",drive="Drive /c0/e32/s3"} 25.0
megaraid_drive_smart{attribute="hardware_ecc_recovered",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="udma_crc_error_count",controller="0",drive="Drive /c0/e32/s3"} 0.0
megaraid_drive_smart{attribute="por_recovery_count",controller="0",drive="Drive /c0/e32/s3"} 17.0
megaraid_drive_smart{attribute="total_host_writes",controller="0",drive="Drive /c0/e32/s3"} 2.2070721883e+011
megaraid_drive_smart{attribute="initial_bad_block_count",controller="0",drive="Drive /c0/e32/s3"} 40962.0
# HELP megaraid_virtual_drive_status Virtual drive status (1=Optimal, 0=Other)
# TYPE megaraid_virtual_drive_status gauge
megaraid_virtual_drive_status{controller="0",vd="DG0/VD0"} 1.0
# HELP megaraid_bbu_health Battery Backup Unit health (1=Healthy, 0=Unhealthy)
# TYPE megaraid_bbu_health gauge
megaraid_bbu_health{controller="0"} 1.0
```

This is another Prometheus exporter, but is meant to target machines running ESXi that have a PERC RAID controller. This essentially leverages the [storcli.py](https://github.com/prometheus-community/node-exporter-textfile-collector-scripts/blob/f5c56e75208e5d1ba4ce90b8285e924ec3e17cda/storcli.py) textfile collector's functionality, but does so over `sshpass`. It's scuffed, I know, but it works. I couldn't find anything else that allowed me to fetch the RAID controller's metrics (even if it was just some SMART data).

This tool relies on installing `perccli` on the ESXi machine as a `.vib`, and then the exporter SSHs into the machine to run the command `/opt/lsi/perccli/perccli /cALL show all J` to gather the JSON the command outputs, and then exposes the information for Prometheus to scrape on `/metrics`.

You can also find a `Dockerfile` in this repository if you would like to create a container out of it for yourself. Or you can fetch it via:
```
docker pull perfectra1n/esxi-perccli-exporter:latest
```

Otherwise, you can run:

```bash
cd esxi-perccli-exporter/
pip install -r requirements.txt
python main.py
```
in order to just run the exporter on `10424`. You'll probably need to set the `CONFIG_FILE_PATH` environment variable to the path where your config is stored, though.

You'll need the following:

- Enable remote SSH on the ESXi hosts
- Install `perccli` on the remote machine. You can download it [here](https://dl.dell.com/FOLDER04470715M/1/perccli_7.1-007.0127_linux.tar.gz), or you can find the driver page (if the previous direct URL didn't work) for it [here](https://www.dell.com/support/home/en-us/drivers/driversdetails?driverid=f48c2).
  - For ESXi, you can find the `.vib` version of the `perccli` [here](https://dl.dell.com/FOLDER04827986M/1/VMware_PERCCLI_6WTDV_7.3-007.0318.tar.gz), with the driver page (if the previous direct URL didn't work) for it [here](https://www.dell.com/support/home/en-us/drivers/driversdetails?driverid=6wtdv).
  - You can then use `sftp` to copy the `.vib` file over into something like the `/tmp` directory on the ESXi host.
  - Then install the `.vib` via `esxcli software vib install -v=/tmp/vmware-perccli-007.1327.vib --force --maintenance-mode --no-sig-check` (make sure you use the correct filename, and provide the full path to the file in the `-v` argument).
  - You can then validate that the installation worked as expected, by running the following command on the ESXi machine:
```bash
/opt/lsi/perccli/perccli /cALL show all J
```
  - Then you're good to go!

### Configuration

Here's an example of the `config.yml` you'll need to create.

```yaml
targets:
  server1:
    username: root
    password: esxi_root_password
  server2:
    username: root
    password: esxi_root_password
```

You can change the following values using environment variables:
- Modify the default port the application exposes by overriding the environment variable `PORT`. (default value of `10424`)
- Define the path where `perccli` is stored on the remote machine by overriding `PERCCLI_FILE_PATH`. (default value of `/opt/lsi/perccli/perccli`)
- Modify the location of the configuration file via the variable `CONFIG_FILE_PATH`. (default value `/etc/prometheus/config.yml`)

Below is a list of the environment variables that you can change, and their defaults:
```yaml
CONFIG_FILE_PATH: "/etc/prometheus/config.yml"
PERCCLI_FILE_PATH: "/opt/lsi/perccli/perccli"
PORT: 10424
```

### Prometheus Scrape Job

You can use the following Prometheus scrape job entry as a basis for your own:

```yaml
    - job_name: "perccli-metrics"
      static_configs:
        - targets:
            - server1
            - server2
            - server3
      metrics_path: /metrics
      relabel_configs:
        - source_labels: [__address__]
          target_label: __param_target
        - source_labels: [__param_target]
          target_label: instance
        - target_label: __address__
          replacement: <real_ip_of_exporter>:<real_port_of_exporter>
```
