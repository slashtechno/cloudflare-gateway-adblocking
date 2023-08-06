import os
import pathlib

import requests
from dotenv import load_dotenv

# List Utils


# Convert a hosts file to a simple hostname list
def convert_to_list(file: pathlib.Path) -> list:
    with open(file, "r") as f:
        # Don't read commented lines; strip whitespace; remove 127.0.0.1 from beginning of line; ignore lines with "localhost"; ignore lines with "::1"; ignore newlines            blocklists.extend([i[10:].strip() for i in f.readlines() if not i.startswith('#') and 'localhost' not in i and '::1' not in i])
        hosts = [
            i[10:].strip()
            for i in f.readlines()
            if not i.startswith("#") and "localhost" not in i and "::1" not in i
        ]
        # Equivalent to:
        # for x in f.readlines():
        #     if not x.startswith('#') and 'localhost' not in x and '::1' not in x:
        #         hosts.append(x[10:].strip())
        # If there are any empty strings in the list, remove them since for some reason, whitespace is stil in the list
        hosts = [i for i in hosts if i != ""]
        return hosts


# General Utils


# Load environment variables
def load_env() -> dict:
    load_dotenv(".env")
    if not os.environ.get("CLOUDFLARE_TOKEN") and not os.environ.get(
        "CLOUDFLARE_ACCOUNT_ID"
    ):
        load_dotenv(".envrc")
    if not os.environ.get("CLOUDFLARE_TOKEN") or not os.environ.get(
        "CLOUDFLARE_ACCOUNT_ID"
    ):
        print(
            "No environment variables found. Please create a .env file or .envrc file"
        )
        exit()
    else:
        return {
            "CLOUDFLARE_TOKEN": os.environ.get("CLOUDFLARE_TOKEN"),
            "CLOUDFLARE_ACCOUNT_ID": os.environ.get("CLOUDFLARE_ACCOUNT_ID"),
        }


def get_lists() -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/lists"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        print(f"Error getting lists: {response.text}")
    return response.json()["result"]


def filter_adblock_lists(lists: dict) -> dict:
    adblock_lists = []
    for lst in lists:
        if lst["name"].startswith("adblock-list") and lst["type"] == "DOMAIN":
            adblock_lists.append(lst)
    return adblock_lists


def get_gateway_rules() -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/gateway/rules"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        print(f"Error getting lists: {response.text}")
    return response.json()["result"]


TOKEN = load_env()["CLOUDFLARE_TOKEN"]
ACCOUNT_ID = load_env()["CLOUDFLARE_ACCOUNT_ID"]
