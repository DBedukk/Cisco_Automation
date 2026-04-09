# VPN Automation Tool
Automates the creation of Site-to-Site VPN topologies on Cisco FMC via REST API.

---

## Directory Structure

```
VPN_Automation/
├── src/                          # Python scripts
│   ├── FMC_Object_Import.py      # Step 1 — import network objects into FMC
│   ├── FMC_Object_Delete.py      # Utility — delete imported objects (reset)
│   └── Site_to_Site_VPN_Automation.py  # Step 2 — create VPN topologies
├── data/
│   ├── objects/                  # CSVs for network object import
│   │   └── ccf_objects_clean_desc2_RENAMED.csv
│   └── vpn/                      # CSVs for VPN topology creation
│       ├── vpn_lab_test.csv      # Lab/testing topology
│       └── vpn_port_huron.csv    # Production topology
├── output/                       # Auto-generated result files (JSON/CSV)
├── docs/                         # Reference documentation
├── venv/                         # Python virtual environment
└── README.md
```

---

## Requirements

- Python 3.10+
- Cisco FMC reachable over HTTPS
- FMC user with API and admin privileges
- All objects referenced in the VPN CSV must exist in FMC before running Step 2

---

## Setup (First Time Only!!!)

Create and activate the virtual environment, then install dependencies:

```bash
cd /c/Users/Doruk/OneDrive/Desktop/VPN-Tunnel-Auto/VPN_Automation

python -m venv venv

source venv/Scripts/activate

pip install requests pandas PyYAML
```

---

## Workflow

### Step 1 — Activate the Virtual Environment

Always activate the venv before running any script:

```bash
source venv/Scripts/activate
```

---

### Step 2 — Import Network Objects into FMC

This script reads the objects CSV and creates host/network objects in FMC.
- If an object with the same name and IP already exists, it is skipped.
- If an object with the same IP exists under a different name, the old one is deleted and replaced with the new one.

```bash
python src/FMC_Object_Import.py -u admin -s 192.168.10.240 -f data/objects/ccf_objects_clean_desc2_RENAMED.csv
```

You will be prompted for the FMC password.

**Output:** Results saved to `output/object_import_results_<timestamp>.json`

---

### Step 3 — Create VPN Topologies in FMC

This script reads the VPN CSV and creates Site-to-Site VPN Point-to-Point topologies in FMC.
All objects, IKE policies, and devices referenced in the CSV must already exist in FMC.

**Lab/Testing:**
```bash
python src/Site_to_Site_VPN_Automation.py -u admin -s 192.168.10.240 -f data/vpn/vpn_lab_test.csv
```

**Production:**
```bash
python src/Site_to_Site_VPN_Automation.py -u admin -s 192.168.10.240 -f data/vpn/vpn_port_huron.csv
```

You will be prompted for the FMC password.

**Output:** Results saved to `output/results_<timestamp>.json`

---

### Step 4 — Deploy from FMC UI

The scripts only create the configuration in FMC — they do not push it to the device.
To activate the VPN on the firewall:

1. Log into FMC at `https://192.168.10.240`
2. Go to **Deploy > Deployment**
3. Select your FTD device (e.g. `PJM_FTD`)
4. Click **Deploy**

The tunnel status will change from `Unknown` to `Up` once the peer responds.

---

## Utility — Delete Imported Objects (Reset)

To remove all objects that were imported via the objects CSV (does NOT touch default FMC objects):

```bash
python src/FMC_Object_Delete.py -u admin -s 192.168.10.240 -f data/objects/ccf_objects_clean_desc2_RENAMED.csv
```

**Output:** Results saved to `output/object_delete_results_<timestamp>.json`

---

## CSV Formats

### Object Import CSV (`data/objects/`)

| Column | Description | Example |
|---|---|---|
| NAME | Object name in FMC | `ALTA-PARTNERS-REMOTE-10.14.15.15` |
| DESCRIPTION | Description | `Alta Partners remote host` |
| TYPE | `host` or `network` | `host` |
| VALUE | IP address or CIDR | `10.14.15.15` or `10.0.0.0/24` |

### VPN Topology CSV (`data/vpn/`)

| Column | Description | Example |
|---|---|---|
| s2s_policy_name | Name of the VPN topology | `PORT-HURON-MI` |
| ike_version | IKE version | `2` |
| ike_policy | IKE policy name (must exist in FMC) | `IKEv2_Policy_86400_GCM` |
| preshared_key | Pre-shared key for the tunnel | `vpn123` |
| device_name | FTD device name in FMC (Node A) | `CR-DC-02-FTD3130-VPN-FW1` |
| device_interface_name | FTD interface name | `vpn_outside` |
| protected_network_name | Local protected network object | `host.placeholder-169.254.255.250` |
| remote_device_name | Remote peer name (Node B) | `PORT-HURON-MI_PEER` |
| remote_device_ip | Remote peer public IP | `50.218.171.126` |
| is_dynamic_ip | Whether remote IP is dynamic | `FALSE` |
| remote_protected_network_name | Remote protected network object | `host.placeholder-169.254.255.254` |

---

## Optional Flags

| Flag | Description |
|---|---|
| `-u` / `--username` | FMC username (required) |
| `-p` / `--password` | FMC password (optional, prompted if omitted) |
| `-s` / `--fmc_server` | FMC IP address (required) |
| `-f` / `--input_file` | Path to input CSV (required) |
| `-c` / `--cert_path` | Path to FMC certificate for SSL verification |
| `-v` / `--verbose` | Print full HTTP request/response details |
