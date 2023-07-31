# This is a scriprt to undo the changes made by adblock-zerotrust.py

import requests
import utils.utils

# Load environment variables
TOKEN = utils.load_env()["CLOUDFLARE_TOKEN"]
ACCOUNT_ID = utils.load_env()["CLOUDFLARE_ACCOUNT_ID"]


def delete_adblock_list(lists: dict):
    for lst in lists:
        url = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/lists/{lst["id"]}'
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        }
        response = requests.delete(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Error deleting list: {response.text}")
        else:
            print(f'Deleted list {lst["name"]}')


def delete_adblock_policy(policies: dict):
    for policy in policies:
        if policy["name"] == "Block Ads":
            url = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/rules/{policy["id"]}'
            headers = {
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            }
            response = requests.delete(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"Error deleting policy: {response.text}")
            else:
                print(f'Deleted policy {policy["name"]}')
            break
    else:
        print("No policy found")


def main():
    rules = utils.get_gateway_rules()
    delete_adblock_policy(rules)
    lists = utils.get_lists()
    lists = utils.filter_adblock_lists(lists)
    delete_adblock_list(lists)


if __name__ == "__main__":
    main()
