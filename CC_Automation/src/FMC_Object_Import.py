"""
Copyright (c) 2023 Cisco and/or its affiliates.

This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at

               https://developer.cisco.com/docs/licenses

All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.

"""

__author__ = "Doruk Beduk"
__copyright__ = "Copyright (c) 2023 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import json
import os
import sys
import csv
import time
from getpass import getpass
from argparse import ArgumentParser

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output')

import requests

requests.packages.urllib3.disable_warnings()


def verbose_output(func_name, response):
    """
    Print full HTTP response data when verbose option is chosen.
    """
    print(f'{func_name} Response:\nStatus Code: {response.status_code}\nHeaders: {response.headers}\nPayload: {response.text}\n')


def csv_to_json(args):
    """
    Parse the input CSV file into a list of dicts.
    Handles BOM characters automatically via utf-8-sig encoding.
    """
    csv_data = []
    with open(args.input_file, 'rt', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_data.append(row)
    if args.verbose:
        print(f'CSV Data:\n{json.dumps(csv_data, indent=4)}\n')
    return csv_data


def auth(args):
    """
    Authenticate to FMC and return token, refresh_token, domain_uuid.
    """
    if not args.password:
        password = getpass("Please enter the FMC password: ", stream=None)
    else:
        password = args.password

    url = f'https://{args.fmc_server}/api/fmc_platform/v1/auth/generatetoken'
    headers = {'Content-Type': 'application/json'}

    if args.cert_path:
        response = requests.post(url, headers=headers, auth=requests.auth.HTTPBasicAuth(args.username, password), verify=args.cert_path)
    else:
        response = requests.post(url, headers=headers, auth=requests.auth.HTTPBasicAuth(args.username, password), verify=False)

    if args.verbose:
        verbose_output('auth()', response)

    if response.status_code in [200, 204] and response.headers['X-auth-access-token'] != '':
        token = response.headers['X-auth-access-token']
        refresh_token = response.headers['X-auth-refresh-token']
        domain_uuid = response.headers['DOMAIN_UUID']
        return token, refresh_token, domain_uuid
    else:
        print('Error encountered during authentication:\n')
        verbose_output('auth()', response)
        sys.exit(1)


def logout(args, token):
    """
    Revoke the FMC API session token so the session is properly closed.
    """
    url = f'https://{args.fmc_server}/api/fmc_platform/v1/auth/revokeaccess'
    headers = {'Content-Type': 'application/json', 'X-auth-access-token': token}
    verify = args.cert_path if args.cert_path else False
    requests.post(url, headers=headers, verify=verify)
    print('FMC session closed.')


def get_existing_objects(args, token, domain_uuid):
    """
    Retrieve all existing host and network objects from FMC.
    Returns a dict mapping value (IP/network) -> {id, name, type} to detect IP duplicates.
    Paginates through all objects.
    """
    existing = {}
    headers = {
        'Content-Type': 'application/json',
        'X-auth-access-token': token
    }

    for obj_type in ['hosts', 'networks']:
        url = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}'
        offset = 0
        limit = 1000

        while True:
            params = {'limit': limit, 'offset': offset, 'expanded': True}

            if args.cert_path:
                response = requests.get(url, headers=headers, params=params, verify=args.cert_path)
            else:
                response = requests.get(url, headers=headers, params=params, verify=False)

            if args.verbose:
                verbose_output('get_existing_objects()', response)

            if response.status_code == 200 and 'items' in response.json():
                items = response.json()['items']
                for item in items:
                    value = item.get('value', '').strip()
                    if value:
                        existing[value] = {
                            'id': item['id'],
                            'name': item['name'],
                            'type': obj_type  # 'hosts' or 'networks'
                        }
                if len(items) < limit:
                    break
                offset += limit
            else:
                break

    print(f'Found {len(existing)} existing objects in FMC.\n')
    return existing


def delete_object(args, token, domain_uuid, obj_id, obj_type, obj_name):
    """
    Delete an existing host or network object from FMC by ID.
    obj_type should be 'hosts' or 'networks'.
    Returns True on success, False on failure.
    """
    headers = {
        'Content-Type': 'application/json',
        'X-auth-access-token': token
    }
    url = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}/{obj_id}'

    if args.cert_path:
        response = requests.delete(url, headers=headers, verify=args.cert_path)
    else:
        response = requests.delete(url, headers=headers, verify=False)

    if args.verbose:
        verbose_output('delete_object()', response)

    if response.status_code in [200, 204]:
        print(f'DELETED (replaced): {obj_name}')
        return True
    else:
        print(f'!!!!!!!!!!\nFAILED to delete {obj_name}\n!!!!!!!!!!\n')
        verbose_output('delete_object()', response)
        return False


BATCH_SIZE = 50


def build_payload(row, fmc_type):
    return {
        'name': row['NAME'].strip(),
        'description': row['DESCRIPTION'].strip(),
        'value': row['VALUE'].strip(),
        'type': fmc_type
    }


def bulk_create(args, token, domain_uuid, obj_type, batch):
    """
    Create a batch of objects in a single API call using FMC bulk create.
    obj_type is 'hosts' or 'networks'. batch is a list of payload dicts.
    Returns (created_names, failed_names).
    """
    headers = {'Content-Type': 'application/json', 'X-auth-access-token': token}
    url = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}?bulk=true'
    verify = args.cert_path if args.cert_path else False

    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=batch, verify=verify, timeout=60)

            if args.verbose:
                verbose_output('bulk_create()', response)

            # Check rate limit headers
            remaining = response.headers.get('X-RateLimit-Remaining')
            if remaining is not None and int(remaining) < 10:
                print(f'WARNING: Only {remaining} API requests remaining. Pausing 60 seconds...')
                time.sleep(60)

            if response.status_code in [200, 201]:
                created = [obj['name'] for obj in response.json().get('items', batch)]
                for name in created:
                    print(f'CREATED: {name}')
                return created, []
            else:
                print(f'Bulk create failed (attempt {attempt + 1}/3), falling back to individual creates...')
                if args.verbose:
                    verbose_output('bulk_create()', response)
                # Fall back to one-by-one for this batch
                created, failed = [], []
                for payload in batch:
                    ep = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}'
                    r = requests.post(ep, headers=headers, json=payload, verify=verify, timeout=30)
                    if r.status_code in [200, 201]:
                        print(f'CREATED: {payload["name"]}')
                        created.append(payload['name'])
                    else:
                        print(f'!!!!!!!!!!\nFAILED to create: {payload["name"]}\n!!!!!!!!!!\n')
                        if args.verbose:
                            verbose_output('bulk_create() fallback', r)
                        failed.append(payload['name'])
                    time.sleep(0.5)
                return created, failed

        except requests.exceptions.ConnectTimeout:
            print(f'Timeout on bulk create, attempt {attempt + 1}/3. Waiting 10 seconds...')
            time.sleep(10)

    names = [p['name'] for p in batch]
    print(f'!!!!!!!!!!\nGave up on batch of {len(batch)} objects after 3 attempts.\n!!!!!!!!!!\n')
    return [], names


def main(args):
    """
    Main workflow:
    1. Parse CSV
    2. Authenticate to FMC
    3. Get existing objects (to skip duplicates)
    4. Create all host/network objects from CSV
    5. Print summary
    """
    # Parse input CSV
    input_data = csv_to_json(args)
    print(f'Loaded {len(input_data)} objects from {args.input_file}\n')

    # Authenticate
    token, refresh_token, domain_uuid = auth(args)
    print('Authentication successful.\n')

    # Get existing objects keyed by IP/value
    existing_objects = get_existing_objects(args, token, domain_uuid)

    if args.dry_run:
        print('*** DRY RUN — no changes will be made to FMC ***\n')

    # Create objects — replace only if same IP exists under a different name
    results = {'created': [], 'replaced': [], 'skipped': [], 'failed': []}

    def print_summary():
        prefix = '[DRY RUN] ' if args.dry_run else ''
        print(f'\n{"="*50}')
        print(f'{prefix}SUMMARY')
        print(f'{"="*50}')
        print(f'Total in CSV : {len(input_data)}')
        print(f'Would create : {len(results["created"])}' if args.dry_run else f'Created      : {len(results["created"])}')
        print(f'Would replace: {len(results["replaced"])}' if args.dry_run else f'Replaced     : {len(results["replaced"])}')
        print(f'Skipped      : {len(results["skipped"])}')
        print(f'Failed       : {len(results["failed"])}')

        if results['failed']:
            print(f'\nFailed objects:')
            for n in results['failed']:
                print(f'  - {n}')

        if not args.dry_run:
            timestamp = time.strftime("%Y-%m-%d_%I-%M-%S%p_%Z", time.localtime())
            filename = os.path.join(OUTPUT_DIR, f'object_import_results_{timestamp}.json')
            with open(filename, 'w') as f:
                f.write(json.dumps(results, indent=4))
            print(f'\nResults saved to {filename}')
        else:
            print('\n*** DRY RUN complete — nothing was created, replaced, or deleted. ***')

    try:
        # First pass — categorize every row
        to_replace = []   # rows that need delete + create (different name, same IP)
        to_create = {'hosts': [], 'networks': []}  # rows that are brand new

        for row in input_data:
            name = row['NAME'].strip()
            value = row['VALUE'].strip()
            obj_type = row['TYPE'].strip().lower()

            if value in existing_objects:
                existing = existing_objects[value]
                if existing['name'] == name:
                    print(f'SKIPPED (already exists): {name} -> {value}')
                    results['skipped'].append(name)
                else:
                    print(f'WOULD REPLACE: old="{existing["name"]}" new="{name}" IP={value}' if args.dry_run else
                          f'REPLACING (different name, same IP): old="{existing["name"]}" new="{name}" IP={value}')
                    if args.dry_run:
                        results['replaced'].append(name)
                    else:
                        to_replace.append((row, existing))
            else:
                print(f'WOULD CREATE ({obj_type}): {name} -> {value}' if args.dry_run else
                      f'Queued for bulk create ({obj_type}): {name} -> {value}')
                if args.dry_run:
                    results['created'].append(name)
                else:
                    fmc_type = 'Host' if obj_type == 'host' else 'Network'
                    endpoint_type = 'hosts' if obj_type == 'host' else 'networks'
                    to_create[endpoint_type].append(build_payload(row, fmc_type))

        if not args.dry_run:
            # Handle replaces individually (delete old → create new)
            for row, existing in to_replace:
                name = row['NAME'].strip()
                deleted = delete_object(args, token, domain_uuid, existing['id'], existing['type'], existing['name'])
                if not deleted:
                    results['failed'].append(name)
                    time.sleep(0.5)
                    continue
                time.sleep(0.3)
                obj_type = row['TYPE'].strip().lower()
                fmc_type = 'Host' if obj_type == 'host' else 'Network'
                endpoint_type = 'hosts' if obj_type == 'host' else 'networks'
                payload = build_payload(row, fmc_type)
                created, failed = bulk_create(args, token, domain_uuid, endpoint_type, [payload])
                results['replaced'].extend(created)
                results['failed'].extend(failed)
                time.sleep(0.5)

            # Handle new creates in batches of BATCH_SIZE
            for obj_type, payloads in to_create.items():
                if not payloads:
                    continue
                print(f'\nCreating {len(payloads)} new {obj_type} in batches of {BATCH_SIZE}...')
                for i in range(0, len(payloads), BATCH_SIZE):
                    batch = payloads[i:i + BATCH_SIZE]
                    created, failed = bulk_create(args, token, domain_uuid, obj_type, batch)
                    results['created'].extend(created)
                    results['failed'].extend(failed)
                    time.sleep(0.5)

    except KeyboardInterrupt:
        print('\n\nInterrupted by user.')

    print_summary()
    logout(args, token)


if __name__ == "__main__":
    parser = ArgumentParser(description='Bulk import host and network objects into Cisco FMC from a CSV file. If an existing object shares the same IP as a CSV row, the old object is deleted and replaced with the new one.')
    parser.add_argument('--username', '-u', type=str, required=True, help='FMC Username')
    parser.add_argument('--password', '-p', type=str, required=False, help='FMC Password')
    parser.add_argument('--fmc_server', '-s', type=str, required=True, help='FMC Server IP')
    parser.add_argument('--cert_path', '-c', type=str, required=False, help='Path to FMC cert for verification.')
    parser.add_argument('--input_file', '-f', type=str, required=True, help='CSV file with NAME, DESCRIPTION, TYPE, VALUE columns.')
    parser.add_argument('--verbose', '-v', action='store_true', help='Print verbose output')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Preview what would happen without making any changes to FMC.')
    args = parser.parse_args()

    main(args)
