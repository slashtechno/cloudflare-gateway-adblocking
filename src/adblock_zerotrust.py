import os
import requests
import utils.utils
import pathlib

# Load environment variables
TOKEN = utils.load_env()["CLOUDFLARE_TOKEN"]
ACCOUNT_ID = utils.load_env()["CLOUDFLARE_ACCOUNT_ID"]


def get_blocklists():
    # __file__ is a special variable that is the path to the current file
    list_directory = pathlib.Path(__file__).parent.parent.joinpath("blocklists")
    for file in list_directory.iterdir():
        blocklists = utils.convert_to_list(file)
    return blocklists


def apply_whitelists(blocklists):
    whitelist = utils.convert_to_list(
        pathlib.Path(__file__).parent.parent.joinpath("whitelist.txt")
    )
    blocklists = [x for x in blocklists if x not in whitelist]
    return blocklists


def split_list(blocklists):
    lists = []
    lists.extend(
        [blocklists[i : i + 1000] for i in range(0, len(blocklists), 1000)]
    )  # This is the same as the for loop below
    # for i in range(0, len(blocklists), 1000):
    #     # This is appending a list of 1000 domains to the lists list. It is doing this by slicing the blocklists list to get the first 1000 domains, then the next 1000 domains, etc.
    #     lists.append(blocklists[i:i + 1000])
    return lists


def upload_to_cloudflare(lists):
    # A: It's iterating over the lists and uploading them to Cloudflare, the enumerate function is used to get the index of the list since lists is a list of lists
    for i, lst in enumerate(lists):
        list_name = f"adblock-list-{i + 1}"
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/lists"
        )
        headers = {
            "Authorization": f"Bearer {TOKEN}",
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
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"Uploaded {list_name} to Cloudflare")
        if response.status_code != 200:
            print(f"Error uploading {list_name}: {response.text}")


def create_dns_policy(lists):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/rules"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
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
    blocklists = get_blocklists()
    blocklists = apply_whitelists(blocklists)
    lists = split_list(blocklists)
    upload_to_cloudflare(lists)
    cloud_lists = utils.get_lists()
    cloud_lists = utils.filter_adblock_lists(cloud_lists)
    create_dns_policy(cloud_lists)


if __name__ == "__main__":
    main()
