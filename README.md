# (Remote) ESXi PERCCLI Exporter

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
