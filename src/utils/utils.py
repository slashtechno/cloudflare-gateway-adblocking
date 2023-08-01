import os
import pathlib

import requests
from dotenv import load_dotenv


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
        else:
            url = "https://api.cloudflare.com/client/v4/user/tokens/verify"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            response = requests.get(url, headers=headers, timeout=10)
            if response.json()["success"] is False:
                raise ValueError("Invalid token")
            else:
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
        else:
            # Possibly make a request to lists to check if the account ID exists
            self._account_id = account_id


# List Utils


# Convert a hosts file to a simple hostname list
def convert_to_list(file: pathlib.Path) -> list:
    with open(file, "r") as f:
        # Don't read commented lines; strip whitespace;
        #  remove 127.0.0.1 from beginning of line;
        # ignore lines with "localhost"; ignore lines with "::1";
        # ignore newlines
        hosts = [
            i[10:].strip()
            for i in f.readlines()
            if not i.startswith("#") and "localhost" not in i and "::1" not in i
        ]
        # Equivalent to:
        # for x in f.readlines():
        #     if not x.startswith('#') and 'localhost' not in x and '::1' not in x:
        #         hosts.append(x[10:].strip())

        # If there are any empty strings in the list, remove them
        # For some reason, whitelist seems to still be present
        hosts = [i for i in hosts if i != ""]
        return hosts


# General Utils


def get_lists(account_id, token) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/lists"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=10)
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


def get_gateway_rules(account_id, token) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/gateway/rules"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        print(f"Error getting lists: {response.text}")
    return response.json()["result"]
