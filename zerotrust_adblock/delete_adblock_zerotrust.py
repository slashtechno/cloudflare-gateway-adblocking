# This is a scriprt to undo the changes made by adblock-zerotrust.py

import requests
import os
import utils

# Load environment variables
TOKEN = utils.load_env()['CLOUDFLARE_TOKEN']
ACCOUNT_ID = utils.load_env()['CLOUDFLARE_ACCOUNT_ID']


def delete_adblock_list(lists: dict):
    for lst in lists:
        url = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/lists/{lst["id"]}'
        headers = {
            'Authorization': f'Bearer {TOKEN}',
            "Content-Type": "application/json",
        }
        response = requests.delete(url, headers=headers)
        if response.status_code != 200:
            print(f'Error deleting list: {response.text}')
        else:
            print(f'Deleted list {lst["name"]}')
def main():
    lists = utils.get_lists()
    lists = utils.filter_adblock_lists(lists)
    delete_adblock_list(lists)

if __name__ == '__main__':
    main()