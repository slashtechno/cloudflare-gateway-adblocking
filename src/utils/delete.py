import asyncio

import httpx
import requests

from . import utils


async def delete_adblock_list(lists: dict, account_id: str, token: str, timeout:int = 10):
    try:
        async with httpx.AsyncClient() as client:
            for lst in lists:
                url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists/{lst["id"]}'
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                response = await client.delete(url, headers=headers, timeout=timeout)
                if response.status_code != 200:
                    print(f"Error deleting list: {response.text}")
                else:
                    print(f'Deleted list {lst["name"]}')
    except TypeError as e:
        if str(e) == "'NoneType' object is not iterable":
            print("No lists found")
        else:
            raise e


def delete_adblock_policy(policies: dict, account_id: str, token: str, timeout:int = 10):
    for policy in policies:
        if policy["name"] == "Block Ads":
            url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/rules/{policy["id"]}'
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            response = requests.delete(url, headers=headers, timeout=timeout)
            if response.status_code != 200:
                print(f"Error deleting policy: {response.text}")
            else:
                print(f'Deleted policy {policy["name"]}')
            break
    else:
        print("No policy found")


def main():
    account_id = input("Enter your Cloudflare account ID: ")
    token = input("Enter your Cloudflare API token: ")

    rules = utils.get_gateway_rules(account_id, token)
    asyncio.run(utils.delete_adblock_list(rules, account_id, token))
    lists = utils.get_lists(account_id, token)
    lists = utils.filter_adblock_lists(lists)
    delete_adblock_list(lists, account_id, token)


if __name__ == "__main__":
    main()
