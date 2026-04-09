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
    print(f'{func_name} Response:\nStatus Code: {response.status_code}\nHeaders: {response.headers}\nPayload: {response.text}\n')


def csv_to_json(args):
    csv_data = []
    with open(args.input_file, 'rt', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_data.append(row)
    return csv_data


def auth(args):
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

    if response.status_code in [200, 204] and response.headers['X-auth-access-token'] != '':
        return response.headers['X-auth-access-token'], response.headers['X-auth-refresh-token'], response.headers['DOMAIN_UUID']
    else:
        print('Error during authentication:')
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
    Returns a dict mapping object name (lowercase) -> {id, type}
    so we can look up FMC objects by the names in the CSV.
    Paginates through all host and network objects.
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
            params = {'limit': limit, 'offset': offset}

            if args.cert_path:
                response = requests.get(url, headers=headers, params=params, verify=args.cert_path)
            else:
                response = requests.get(url, headers=headers, params=params, verify=False)

            if args.verbose:
                verbose_output('get_existing_objects()', response)

            if response.status_code == 200 and 'items' in response.json():
                items = response.json()['items']
                for item in items:
                    existing[item['name'].lower()] = {
                        'id': item['id'],
                        'name': item['name'],
                        'type': obj_type
                    }
                if len(items) < limit:
                    break
                offset += limit
            else:
                break

    print(f'Found {len(existing)} existing objects in FMC.\n')
    return existing


BATCH_SIZE = 50  # Number of objects to delete per API request


def bulk_delete(args, token, domain_uuid, obj_type, batch):
    """
    Delete a batch of objects in a single API call using FMC bulk delete.
    batch is a list of {id, name} dicts, all of the same obj_type.
    Returns (deleted_names, failed_names).
    """
    headers = {
        'Content-Type': 'application/json',
        'X-auth-access-token': token
    }
    ids = ','.join(obj['id'] for obj in batch)
    url = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}?bulk=true&filter=ids:{ids}'

    verify = args.cert_path if args.cert_path else False
    response = requests.delete(url, headers=headers, verify=verify)

    if args.verbose:
        verbose_output('bulk_delete()', response)

    # Check rate limit headers and warn if running low
    remaining = response.headers.get('X-RateLimit-Remaining')
    if remaining is not None and int(remaining) < 10:
        print(f'WARNING: Only {remaining} API requests remaining in this window. Pausing 60 seconds...')
        time.sleep(60)

    if response.status_code in [200, 204]:
        names = [obj['name'] for obj in batch]
        for name in names:
            print(f'DELETED: {name}')
        return names, []
    else:
        # Bulk failed — fall back to individual deletes for this batch
        print(f'Bulk delete failed for batch, falling back to individual deletes...')
        if args.verbose:
            verbose_output('bulk_delete()', response)
        deleted, failed = [], []
        for obj in batch:
            single_url = f'https://{args.fmc_server}/api/fmc_config/v1/domain/{domain_uuid}/object/{obj_type}/{obj["id"]}'
            r = requests.delete(single_url, headers=headers, verify=verify)
            if r.status_code in [200, 204]:
                print(f'DELETED: {obj["name"]}')
                deleted.append(obj['name'])
            else:
                print(f'!!!!!!!!!!\nFAILED to delete {obj["name"]}\n!!!!!!!!!!\n')
                if args.verbose:
                    verbose_output('bulk_delete() fallback', r)
                failed.append(obj['name'])
            time.sleep(0.5)
        return deleted, failed


def main(args):
    input_data = csv_to_json(args)
    print(f'Loaded {len(input_data)} objects from {args.input_file}\n')

    token, refresh_token, domain_uuid = auth(args)
    print('Authentication successful.\n')

    existing_objects = get_existing_objects(args, token, domain_uuid)

    results = {'deleted': [], 'not_found': [], 'failed': []}

    def print_summary():
        print(f'\n{"="*50}')
        print(f'SUMMARY')
        print(f'{"="*50}')
        print(f'Total in CSV : {len(input_data)}')
        print(f'Deleted      : {len(results["deleted"])}')
        print(f'Not found    : {len(results["not_found"])}')
        print(f'Failed       : {len(results["failed"])}')

        if results['failed']:
            print(f'\nFailed objects:')
            for n in results['failed']:
                print(f'  - {n}')

        timestamp = time.strftime("%Y-%m-%d_%I-%M-%S%p_%Z", time.localtime())
        filename = os.path.join(OUTPUT_DIR, f'object_delete_results_{timestamp}.json')
        with open(filename, 'w') as f:
            f.write(json.dumps(results, indent=4))
        print(f'\nResults saved to {filename}')

    try:
        # Separate objects into hosts and networks, skip not found
        to_delete = {'hosts': [], 'networks': []}
        for row in input_data:
            name = row['NAME'].strip()
            if name.lower() not in existing_objects:
                print(f'NOT FOUND (skipping): {name}')
                results['not_found'].append(name)
            else:
                obj = existing_objects[name.lower()]
                to_delete[obj['type']].append({'id': obj['id'], 'name': obj['name']})

        # Bulk delete in batches per object type
        for obj_type, objects in to_delete.items():
            if not objects:
                continue
            print(f'\nDeleting {len(objects)} {obj_type} in batches of {BATCH_SIZE}...')
            for i in range(0, len(objects), BATCH_SIZE):
                batch = objects[i:i + BATCH_SIZE]
                deleted, failed = bulk_delete(args, token, domain_uuid, obj_type, batch)
                results['deleted'].extend(deleted)
                results['failed'].extend(failed)
                time.sleep(0.5)

    except KeyboardInterrupt:
        print('\n\nInterrupted by user.')

    print_summary()
    logout(args, token)


if __name__ == "__main__":
    parser = ArgumentParser(description='Delete FMC host/network objects listed in a CSV file. Only deletes objects whose names match the CSV — default FMC objects are untouched.')
    parser.add_argument('--username', '-u', type=str, required=True, help='FMC Username')
    parser.add_argument('--password', '-p', type=str, required=False, help='FMC Password')
    parser.add_argument('--fmc_server', '-s', type=str, required=True, help='FMC Server IP')
    parser.add_argument('--cert_path', '-c', type=str, required=False, help='Path to FMC cert for verification.')
    parser.add_argument('--input_file', '-f', type=str, required=True, help='CSV file with NAME column (same format as import CSV).')
    parser.add_argument('--verbose', '-v', action='store_true', help='Print verbose output')
    args = parser.parse_args()

    main(args)
