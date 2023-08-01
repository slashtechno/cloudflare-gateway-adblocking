# This is a scriprt to undo the changes made by adblock-zerotrust.py

import requests
import utils


def delete_adblock_list(lists: dict, account_id: str, token: str):
    for lst in lists:
        url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists/{lst["id"]}'
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response = requests.delete(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Error deleting list: {response.text}")
        else:
            print(f'Deleted list {lst["name"]}')


def delete_adblock_policy(policies: dict, account_id: str, token: str):
    for policy in policies:
        if policy["name"] == "Block Ads":
            url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/rules/{policy["id"]}'
            headers = {
                "Authorization": f"Bearer {token}",
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
