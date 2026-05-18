import xml.etree.ElementTree as ET
import csv
import sys
from datetime import datetime


def parse_paloalto_interfaces(config_file):
    """
    Parses a Palo Alto XML configuration file and extracts:
    - Physical Interfaces (ethernet1/x)
    - Sub-Interfaces (ethernet1/x.y)
    - Aggregate Ethernet / Port-Channels (ae1, ae1.x)
    """

    try:
        tree = ET.parse(config_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"[ERROR] Failed to parse XML: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"[ERROR] File not found: {config_file}")
        sys.exit(1)

    interfaces = []

    # -------------------------------------------------------
    # XPath targets for interface sections in PAN-OS XML
    # -------------------------------------------------------
    ethernet_path   = ".//devices/entry/network/interface/ethernet"
    aggregate_path  = ".//devices/entry/network/interface/aggregate-ethernet"

    # -------------------------------------------------------
    # PARSE PHYSICAL & SUB-INTERFACES (ethernet)
    # -------------------------------------------------------
    ethernet_section = root.find(ethernet_path)
    if ethernet_section is not None:
        for eth_entry in ethernet_section.findall("entry"):
            eth_name = eth_entry.get("name", "Unknown")

            # --- Determine physical interface type ---
            if eth_entry.find("layer3") is not None:
                mode = "Layer3"
            elif eth_entry.find("layer2") is not None:
                mode = "Layer2"
            elif eth_entry.find("virtual-wire") is not None:
                mode = "Virtual Wire"
            elif eth_entry.find("tap") is not None:
                mode = "TAP"
            elif eth_entry.find("ha") is not None:
                mode = "HA"
            elif eth_entry.find("aggregate-group") is not None:
                agg_group = eth_entry.find("aggregate-group").text
                mode = f"Aggregate Member ({agg_group})"
            else:
                mode = "Unconfigured"

            ip_address  = get_ip_address(eth_entry)
            comment     = get_comment(eth_entry)
            link_speed  = get_element_text(eth_entry, "link-speed", "auto")
            link_duplex = get_element_text(eth_entry, "link-duplex", "auto")
            mtu         = get_element_text(eth_entry, ".//mtu", "1500")

            interfaces.append({
                "Interface Name"     : eth_name,
                "Interface Type"     : "Physical",
                "Mode"               : mode,
                "IP Address"         : ip_address,
                "Bonded Members"     : "N/A",
                "MTU"                : mtu,
                "Link Speed"         : link_speed,
                "Link Duplex"        : link_duplex,
                "Comment/Description": comment,
                "Zone"               : "",
                "Virtual Router"     : "",
            })

            # --- SUB-INTERFACES under this physical ---
            layer3 = eth_entry.find("layer3")
            if layer3 is not None:
                units = layer3.find("units")
                if units is not None:
                    for unit in units.findall("entry"):
                        sub_name    = unit.get("name", "Unknown")
                        sub_ip      = get_ip_address(unit)
                        sub_tag     = get_element_text(unit, "tag", "N/A")
                        sub_mtu     = get_element_text(unit, "mtu", "1500")
                        sub_comment = get_comment(unit)

                        interfaces.append({
                            "Interface Name"     : sub_name,
                            "Interface Type"     : "Sub-Interface",
                            "Mode"               : f"Layer3 (VLAN Tag: {sub_tag})",
                            "IP Address"         : sub_ip,
                            "Bonded Members"     : "N/A",
                            "MTU"                : sub_mtu,
                            "Link Speed"         : "Inherited",
                            "Link Duplex"        : "Inherited",
                            "Comment/Description": sub_comment,
                            "Zone"               : "",
                            "Virtual Router"     : "",
                        })

    # -------------------------------------------------------
    # PARSE AGGREGATE ETHERNET (Port-Channels)
    # -------------------------------------------------------
    aggregate_section = root.find(aggregate_path)
    if aggregate_section is not None:
        for ae_entry in aggregate_section.findall("entry"):
            ae_name = ae_entry.get("name", "Unknown")

            if ae_entry.find("layer3") is not None:
                ae_mode = "Layer3"
            elif ae_entry.find("layer2") is not None:
                ae_mode = "Layer2"
            else:
                ae_mode = "Unconfigured"

            ae_comment  = get_comment(ae_entry)
            ae_mtu      = get_element_text(ae_entry, ".//mtu", "1500")
            lacp_mode   = get_element_text(ae_entry, ".//lacp/mode", "N/A")

            # --- Collect bonded physical members ---
            bonded_members = get_bonded_members(root, ae_name)

            interfaces.append({
                "Interface Name"     : ae_name,
                "Interface Type"     : "Port-Channel (AggregateEthernet)",
                "Mode"               : ae_mode,
                "IP Address"         : get_ip_address(ae_entry),
                "Bonded Members"     : bonded_members,
                "MTU"                : ae_mtu,
                "Link Speed"         : f"LACP Mode: {lacp_mode}",
                "Link Duplex"        : "N/A",
                "Comment/Description": ae_comment,
                "Zone"               : "",
                "Virtual Router"     : "",
            })

            # --- AE Sub-Interfaces ---
            ae_layer3 = ae_entry.find("layer3")
            if ae_layer3 is not None:
                ae_units = ae_layer3.find("units")
                if ae_units is not None:
                    for unit in ae_units.findall("entry"):
                        sub_name    = unit.get("name", "Unknown")
                        sub_ip      = get_ip_address(unit)
                        sub_tag     = get_element_text(unit, "tag", "N/A")
                        sub_mtu     = get_element_text(unit, "mtu", "1500")
                        sub_comment = get_comment(unit)

                        interfaces.append({
                            "Interface Name"     : sub_name,
                            "Interface Type"     : "Sub-Interface (AE)",
                            "Mode"               : f"Layer3 (VLAN Tag: {sub_tag})",
                            "IP Address"         : sub_ip,
                            "Bonded Members"     : f"Inherited from {ae_name}",
                            "MTU"                : sub_mtu,
                            "Link Speed"         : "Inherited",
                            "Link Duplex"        : "Inherited",
                            "Comment/Description": sub_comment,
                            "Zone"               : "",
                            "Virtual Router"     : "",
                        })
    vlan_ifaces = parse_vlan_interfaces(root)
    interfaces.extend(vlan_ifaces)
    # -------------------------------------------------------
    # SECOND PASS — Enrich with Zone & Virtual Router data
    # -------------------------------------------------------
    interfaces = enrich_with_zones(root, interfaces)
    interfaces = enrich_with_virtual_routers(root, interfaces)

    return interfaces


# -------------------------------------------------------
# HELPER: Find all physical interfaces bonded to an AE
# -------------------------------------------------------
def get_bonded_members(root, ae_name):
    """
    Scans all ethernet interfaces to find which ones
    have an aggregate-group matching the given AE name.
    """
    members = []
    ethernet_path    = ".//devices/entry/network/interface/ethernet"
    ethernet_section = root.find(ethernet_path)

    if ethernet_section is not None:
        for eth_entry in ethernet_section.findall("entry"):
            agg_group = eth_entry.find("aggregate-group")
            if agg_group is not None and agg_group.text:
                if agg_group.text.strip() == ae_name:
                    members.append(eth_entry.get("name", "Unknown"))

    return ", ".join(members) if members else "No Members Found"


# -------------------------------------------------------
# HELPER: Get IP Address from interface entry
# -------------------------------------------------------
def get_ip_address(entry):
    ip_list = []

    # Layer3 direct IP
    for ip_entry in entry.findall(".//layer3/ip/entry"):
        ip = ip_entry.get("name", "")
        if ip:
            ip_list.append(ip)

    # Sub-interface direct IP
    for ip_entry in entry.findall(".//ip/entry"):
        ip = ip_entry.get("name", "")
        if ip:
            ip_list.append(ip)

    # DHCP
    if entry.find(".//layer3/dhcp-client") is not None:
        ip_list.append("DHCP")
    if entry.find(".//dhcp-client") is not None:
        ip_list.append("DHCP")

    return ", ".join(ip_list) if ip_list else "N/A"


# -------------------------------------------------------
# HELPER: Get comment/description
# -------------------------------------------------------
def get_comment(entry):
    comment = entry.find("comment")
    if comment is not None and comment.text:
        return comment.text.strip()
    return "N/A"


# -------------------------------------------------------
# HELPER: Get text of a child element safely
# -------------------------------------------------------
def get_element_text(entry, path, default="N/A"):
    el = entry.find(path)
    if el is not None and el.text:
        return el.text.strip()
    return default


# -------------------------------------------------------
# ENRICH: Match interfaces to Security Zones
# -------------------------------------------------------
def enrich_with_zones(root, interfaces):
    zone_map  = {}
    zones_path = ".//devices/entry/vsys/entry/zone"

    for zone in root.findall(zones_path):
        zone_name = zone.get("name", "Unknown")
        for member in zone.findall(".//network/layer3/member"):
            if member.text:
                zone_map[member.text.strip()] = zone_name
        for member in zone.findall(".//network/layer2/member"):
            if member.text:
                zone_map[member.text.strip()] = zone_name
        for member in zone.findall(".//network/virtual-wire/member"):
            if member.text:
                zone_map[member.text.strip()] = zone_name
        for member in zone.findall(".//network/tap/member"):
            if member.text:
                zone_map[member.text.strip()] = zone_name

    for iface in interfaces:
        iface["Zone"] = zone_map.get(iface["Interface Name"], "Unassigned")

    return interfaces


# -------------------------------------------------------
# ENRICH: Match interfaces to Virtual Routers
# -------------------------------------------------------
def enrich_with_virtual_routers(root, interfaces):
    vr_map  = {}
    vr_path = ".//devices/entry/network/virtual-router"

    for vr in root.findall(vr_path):
        vr_name = vr.get("name", "Unknown")
        for member in vr.findall(".//interface/member"):
            if member.text:
                vr_map[member.text.strip()] = vr_name

    for iface in interfaces:
        iface["Virtual Router"] = vr_map.get(iface["Interface Name"], "N/A")

    return interfaces
# -------------------------------------------------------
# PARSE VLAN VIRTUAL INTERFACES (SVI-style)
# -------------------------------------------------------
def parse_vlan_interfaces(root):
    """
    Parses VLAN virtual interfaces from:
    .//devices/entry/network/interface/vlan
    These are SVI-style Layer3 VLAN interfaces that reference
    AE sub-interfaces as their bound members.
    """
    vlan_interfaces = []
    vlan_path       = ".//devices/entry/network/interface/vlan"
    vlan_section    = root.find(vlan_path)

    if vlan_section is not None:
        for vlan_entry in vlan_section.findall("entry"):
            vlan_name = vlan_entry.get("name", "Unknown")

            # --- Get the virtual interface name (e.g., vlan.4090) ---
            svi_interface = get_element_text(
                vlan_entry,
                "virtual-interface/interface",
                "N/A"
            )

            # --- Get bound member interfaces (e.g., ae1.4090) ---
            bound_members = []
            iface_section = vlan_entry.find("interface")
            if iface_section is not None:
                for member in iface_section.findall("member"):
                    if member.text:
                        bound_members.append(member.text.strip())

            vlan_interfaces.append({
                "Interface Name"     : svi_interface,
                "Interface Type"     : "VLAN Interface (SVI)",
                "Mode"               : f"Layer3 VLAN (Name: {vlan_name})",
                "IP Address"         : "N/A",
                "Bonded Members"     : ", ".join(bound_members) if bound_members else "N/A",
                "MTU"                : "1500",
                "Link Speed"         : "N/A",
                "Link Duplex"        : "N/A",
                "Comment/Description": f"VLAN Group: {vlan_name}",
                "Zone"               : "",
                "Virtual Router"     : "",
            })

    return vlan_interfaces

# -------------------------------------------------------
# EXPORT: Print summary to console
# -------------------------------------------------------
def print_summary(interfaces):
    physical   = [i for i in interfaces if i["Interface Type"] == "Physical"]
    sub_ifaces = [i for i in interfaces if "Sub-Interface" in i["Interface Type"]]
    port_ch    = [i for i in interfaces if "Port-Channel" in i["Interface Type"]]
    vlan_ifaces = [i for i in interfaces if "VLAN Interface" in i["Interface Type"]]

    print("\n" + "="*70)
    print("       PALO ALTO INTERFACE INVENTORY SUMMARY")
    print("="*70)
    print(f"  Total Physical Interfaces   : {len(physical)}")
    print(f"  Total Sub-Interfaces        : {len(sub_ifaces)}")
    print(f"  Total Port-Channels (AE)    : {len(port_ch)}")
    print(f"  TOTAL INTERFACES            : {len(interfaces)}")
    print("="*70)

    # --- Physical Interfaces ---
    print(f"\n--- PHYSICAL INTERFACES ({len(physical)}) ---")
    for i in physical:
        print(f"\n  [{i['Interface Name']}]")
        print(f"    Type          : {i['Interface Type']}")
        print(f"    Mode          : {i['Mode']}")
        print(f"    IP Address    : {i['IP Address']}")
        print(f"    MTU           : {i['MTU']}")
        print(f"    Zone          : {i['Zone']}")
        print(f"    Virtual Router: {i['Virtual Router']}")
        print(f"    Description   : {i['Comment/Description']}")

    # --- Sub-Interfaces ---
    print(f"\n--- SUB-INTERFACES ({len(sub_ifaces)}) ---")
    for i in sub_ifaces:
        print(f"\n  [{i['Interface Name']}]")
        print(f"    Type          : {i['Interface Type']}")
        print(f"    Mode          : {i['Mode']}")
        print(f"    IP Address    : {i['IP Address']}")
        print(f"    MTU           : {i['MTU']}")
        print(f"    Zone          : {i['Zone']}")
        print(f"    Virtual Router: {i['Virtual Router']}")
        print(f"    Description   : {i['Comment/Description']}")

    # --- Port-Channels ---
    print(f"\n--- PORT-CHANNELS (Aggregate Ethernet) ({len(port_ch)}) ---")
    for i in port_ch:
        print(f"\n  [{i['Interface Name']}]")
        print(f"    Type          : {i['Interface Type']}")
        print(f"    Mode          : {i['Mode']}")
        print(f"    Bonded Members: {i.get('Bonded Members', 'N/A')}")
        print(f"    IP Address    : {i['IP Address']}")
        print(f"    MTU           : {i['MTU']}")
        print(f"    Link Speed    : {i['Link Speed']}")
        print(f"    Zone          : {i['Zone']}")
        print(f"    Virtual Router: {i['Virtual Router']}")
        print(f"    Description   : {i['Comment/Description']}")

    # --- VLAN Interfaces ---
    print(f"\n--- VLAN INTERFACES / SVI ({len(vlan_ifaces)}) ---")
    for i in vlan_ifaces:
        print(f"\n  [{i['Interface Name']}]")
        print(f"    Type          : {i['Interface Type']}")
        print(f"    Mode          : {i['Mode']}")
        print(f"    Bound Members : {i.get('Bonded Members', 'N/A')}")
        print(f"    IP Address    : {i['IP Address']}")
        print(f"    MTU           : {i['MTU']}")
        print(f"    Zone          : {i['Zone']}")
        print(f"    Virtual Router: {i['Virtual Router']}")
        print(f"    Description   : {i['Comment/Description']}")

# -------------------------------------------------------
# EXPORT: Write summary to TXT file
# -------------------------------------------------------
def export_to_txt(interfaces, output_file):
    physical   = [i for i in interfaces if i["Interface Type"] == "Physical"]
    sub_ifaces = [i for i in interfaces if "Sub-Interface" in i["Interface Type"]]
    port_ch    = [i for i in interfaces if "Port-Channel" in i["Interface Type"]]

    with open(output_file, "w") as f:
        f.write("="*70 + "\n")
        f.write("       PALO ALTO INTERFACE INVENTORY SUMMARY\n")
        f.write("="*70 + "\n")
        f.write(f"  Generated On                : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"  Total Physical Interfaces   : {len(physical)}\n")
        f.write(f"  Total Sub-Interfaces        : {len(sub_ifaces)}\n")
        f.write(f"  Total Port-Channels (AE)    : {len(port_ch)}\n")
        f.write(f"  TOTAL INTERFACES            : {len(interfaces)}\n")
        f.write("="*70 + "\n")

        # --- Physical Interfaces ---
        f.write(f"\n--- PHYSICAL INTERFACES ({len(physical)}) ---\n")
        for i in physical:
            f.write(f"\n  [{i['Interface Name']}]\n")
            f.write(f"    Type          : {i['Interface Type']}\n")
            f.write(f"    Mode          : {i['Mode']}\n")
            f.write(f"    IP Address    : {i['IP Address']}\n")
            f.write(f"    MTU           : {i['MTU']}\n")
            f.write(f"    Zone          : {i['Zone']}\n")
            f.write(f"    Virtual Router: {i['Virtual Router']}\n")
            f.write(f"    Description   : {i['Comment/Description']}\n")

        # --- Sub-Interfaces ---
        f.write(f"\n--- SUB-INTERFACES ({len(sub_ifaces)}) ---\n")
        for i in sub_ifaces:
            f.write(f"\n  [{i['Interface Name']}]\n")
            f.write(f"    Type          : {i['Interface Type']}\n")
            f.write(f"    Mode          : {i['Mode']}\n")
            f.write(f"    IP Address    : {i['IP Address']}\n")
            f.write(f"    MTU           : {i['MTU']}\n")
            f.write(f"    Zone          : {i['Zone']}\n")
            f.write(f"    Virtual Router: {i['Virtual Router']}\n")
            f.write(f"    Description   : {i['Comment/Description']}\n")

        # --- Port-Channels ---
        f.write(f"\n--- PORT-CHANNELS (Aggregate Ethernet) ({len(port_ch)}) ---\n")
        for i in port_ch:
            f.write(f"\n  [{i['Interface Name']}]\n")
            f.write(f"    Type          : {i['Interface Type']}\n")
            f.write(f"    Mode          : {i['Mode']}\n")
            f.write(f"    Bonded Members: {i.get('Bonded Members', 'N/A')}\n")
            f.write(f"    IP Address    : {i['IP Address']}\n")
            f.write(f"    MTU           : {i['MTU']}\n")
            f.write(f"    Link Speed    : {i['Link Speed']}\n")
            f.write(f"    Zone          : {i['Zone']}\n")
            f.write(f"    Virtual Router: {i['Virtual Router']}\n")
            f.write(f"    Description   : {i['Comment/Description']}\n")
        # --- VLAN Interfaces ---
        vlan_ifaces = [i for i in interfaces if "VLAN Interface" in i["Interface Type"]]
        f.write(f"\n--- VLAN INTERFACES / SVI ({len(vlan_ifaces)}) ---\n")
        for i in vlan_ifaces:
            f.write(f"\n  [{i['Interface Name']}]\n")
            f.write(f"    Type          : {i['Interface Type']}\n")
            f.write(f"    Mode          : {i['Mode']}\n")
            f.write(f"    Bound Members : {i.get('Bonded Members', 'N/A')}\n")
            f.write(f"    IP Address    : {i['IP Address']}\n")
            f.write(f"    MTU           : {i['MTU']}\n")
            f.write(f"    Zone          : {i['Zone']}\n")
            f.write(f"    Virtual Router: {i['Virtual Router']}\n")
            f.write(f"    Description   : {i['Comment/Description']}\n")
    print(f"[✔] TXT report exported successfully → {output_file}")


# -------------------------------------------------------
# EXPORT: Write results to CSV
# -------------------------------------------------------
def export_to_csv(interfaces, output_file):
    if not interfaces:
        print("[WARN] No interfaces found to export.")
        return

    fieldnames = [
        "Interface Name", "Interface Type", "Mode",
        "IP Address", "Bonded Members", "MTU",
        "Link Speed", "Link Duplex", "Zone",
        "Virtual Router", "Comment/Description"
    ]

    with open(output_file, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(interfaces)

    print(f"[✔] CSV exported successfully → {output_file}")


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("\nUsage:   palo_interface.py <config.xml> [output_name]")
        print("Example: palo_interface.py running-config.xml my_report\n")
        sys.exit(1)

    config_file = sys.argv[1]

    # --- Build base output name ---
    if len(sys.argv) > 2:
        base_name = sys.argv[2].replace(".csv", "").replace(".txt", "")
    else:
        base_name = f"pa_interface_inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    output_csv = base_name + ".csv"
    output_txt = base_name + ".txt"

    print(f"\n[*] Parsing configuration: {config_file}")
    interfaces = parse_paloalto_interfaces(config_file)

    print_summary(interfaces)
    export_to_csv(interfaces, output_csv)
    export_to_txt(interfaces, output_txt)