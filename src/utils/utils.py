import pathlib
import re

import requests


class Config:
    def __init__(self, token, account_id):
        # Set token using setter
        self.token = token
        if account_id is None:
            raise ValueError("No account ID provided")

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, token):
        if token is None:
            raise ValueError("No token provided")
        url = "https://api.cloudflare.com/client/v4/user/tokens/verify"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.json()["success"] is False:
            raise ValueError("Invalid token")
        # Token needs the following scopes:
        # Zero Trust: Read/Edit
        # Account Firewall Access Rules: Read/Edit
        # Access Apps and Policies: Read/Edit
        self._token = token

    @property
    def account_id(self):
        return self._account_id

    @account_id.setter
    def account_id(self, account_id):
        if account_id is None:
            raise ValueError("No account ID provided")
        # Possibly make a request to lists to check if the account ID exists
        self._account_id = account_id


# List Utils


# Convert a hosts file to a simple hostname list
def convert_to_list(file: pathlib.Path) -> list:
    with open(file, "r") as f:
        # Loop through the file and using regex, only get the domain names
        # Remove the prefixed loopback domain and suffixed comments
        # Remove any empty strings
        loopback = [
            "localhost",
            "::1",
            "localhost.localdomain",
            "broadcasthost",
            "local",
            "ip6-localhost",
            "ip6-loopback",
            "ip6-localnet",
            "ip6-mcastprefix",
            "ip6-allnodes",
            "ip6-allrouters",
            "ip6-allhosts",
            "0.0.0.0",  # skipcq: BAN-B104
        ]
        matches = [
            re.search(r"^(?:127\.0\.0\.1|0\.0\.0\.0|::1)\s+(.+?)(?:\s+#.+)?$", line)
            for line in f
        ]
        hosts = { 
            match.group(1)
            for match in matches
            if match and match.group(1) not in loopback
        }
        # print(f"First 5 hosts: {hosts[:5]}")
        return list(hosts)


# General Utils


def get_lists(account_id, token, timeout = 10) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    if response.status_code != 200:
        print(f"Error getting lists: {response.text}")
    return response.json()["result"]


def filter_adblock_lists(lists: dict) -> dict:
    adblock_lists = []
    try:
        for lst in lists:
            if lst["name"].startswith("adblock-list") and lst["type"] == "DOMAIN":
                adblock_lists.append(lst)
    except TypeError as e:
        if str(e) == "'NoneType' object is not iterable":
            print("No lists found")
        else:
            raise e
    return adblock_lists


def get_gateway_rules(account_id, token, timeout = 10) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/rules"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    if response.status_code != 200:
        print(f"Error getting lists: {response.text}")
    return response.json()["result"]
