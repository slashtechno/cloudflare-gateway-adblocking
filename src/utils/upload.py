import asyncio
import pathlib

import httpx
import requests

from . import utils


def get_blocklists(hosts_path: str = "blocklists"):
    blocklists = []
    hosts_path = pathlib.Path(hosts_path)
    if hosts_path.is_file():
        blocklists = utils.convert_to_list(hosts_path)
    elif hosts_path.is_dir():
        for file in hosts_path.iterdir():
            blocklists.extend(utils.convert_to_list(file))
    else:
        raise ValueError("Invalid hosts file or directory")
    return blocklists


def apply_whitelists(blocklists, whitelist: str = "whitelists"):
    # If whitelist is a file, convert it to a list.
    # If whitelist is a directory, convert all files in it to a list and combine them.
    # If it does not exist, return the original blocklists
    whitelist_path = pathlib.Path(whitelist)
    if whitelist_path.is_file():
        whitelist = utils.convert_to_list(whitelist_path)
    elif whitelist_path.is_dir():
        whitelist = []
        for file in whitelist_path.iterdir():
            whitelist.extend(utils.convert_to_list(file))
    else:
        return blocklists
    blocklists = [x for x in blocklists if x not in whitelist]
    return blocklists


def split_list(blocklists):
    lists = []
    lists.extend(
        [blocklists[i : i + 1000] for i in range(0, len(blocklists), 1000)]
    )  # This is the same as the for loop below
    # for i in range(0, len(blocklists), 1000):
    # This continues to append lists of 1000 items to the lists list via slicing
    #     lists.append(blocklists[i:i + 1000])
    return lists


async def upload_to_cloudflare(lists, account_id: str, token: str) -> None:
    async with httpx.AsyncClient() as client:
        for i, lst in enumerate(lists):
            list_name = f"adblock-list-{i + 1}"
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            data = {
                "name": list_name,
                "type": "DOMAIN",
                "description": "A blocklist of ad domains",
                # Writing this program, I have noticed how powerful list comprehension is.
                "items": [
                    {
                        "value": x,
                    }
                    for x in lst
                ],
            }
            response = await client.post(url, headers=headers, json=data)
            print(f"Uploaded {list_name} to Cloudflare")
            if response.status_code != 200:
                print(f"Error uploading {list_name}: {response.text}")
                exit(1)


def create_dns_policy(lists, account_id: str, token: str) -> None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/rules"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # Construct the traffic string
    traffic = ""
    for i, lst in enumerate(lists):
        if i != 0:
            # ' or ' cannot be seen in the Zero Trs Dashboard
            traffic += " or "
        traffic += f'any(dns.domains[*] in ${lst["id"]})'
    # print(traffic)
    data = {
        "name": "Block Ads",
        "description": "Block ad domains",
        "action": "block",
        "traffic": traffic,
        "enabled": True,
    }
    response = requests.post(url, headers=headers, json=data, timeout=10)
    if response.status_code != 200:
        print(f"Error creating DNS policy: {response.text}")


def main():
    account_id = input("Enter your Cloudflare account ID: ")
    token = input("Enter your Cloudflare API token: ")

    blocklists = get_blocklists()
    blocklists = apply_whitelists(blocklists)
    lists = split_list(blocklists)
    asyncio.run(upload_to_cloudflare(lists, account_id, token))
    cloud_lists = utils.get_lists(account_id, token)
    cloud_lists = utils.filter_adblock_lists(cloud_lists)
    create_dns_policy(cloud_lists, account_id, token)


if __name__ == "__main__":
    main()
