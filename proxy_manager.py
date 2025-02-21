import requests
import json
import time
import subprocess
import threading
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional
import socket
import base64

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='proxy_manager.log'
)

class ProxyManager:
    def __init__(self):
        self.github_repos = [
            "https://raw.githubusercontent.com/repo1/proxy-list/main/proxies.json",
            "https://raw.githubusercontent.com/repo2/free-proxies/master/list.txt"
        ]
        self.custom_urls = [
            "https://example.com/proxy-list",
            "http://myproxies.local/list"
        ]
        self.config_file = "sing-box-config.json"
        self.max_proxies_per_type = 5
        self.timeout = 5  # seconds
        
    def fetch_proxies(self) -> List[Dict]:
        """Fetch proxies from all sources"""
        proxies = []
        
        # Fetch from GitHub repositories
        for url in self.github_repos:
            try:
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                if url.endswith('.json'):
                    proxies.extend(json.loads(response.text))
                else:
                    proxies.extend(self.parse_text_list(response.text))
                logging.info(f"Successfully fetched proxies from {url}")
            except Exception as e:
                logging.error(f"Failed to fetch from {url}: {str(e)}")
                
        # Fetch from custom URLs
        for url in self.custom_urls:
            try:
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                proxies.extend(json.loads(response.text))
                logging.info(f"Successfully fetched proxies from {url}")
            except Exception as e:
                logging.error(f"Failed to fetch from {url}: {str(e)}")
                
        return proxies

    def parse_text_list(self, text: str) -> List[Dict]:
        """Parse plain text proxy list"""
        proxies = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                try:
                    if '://' in line:
                        protocol, rest = line.split('://', 1)
                        proxies.append({
                            "protocol": protocol,
                            "server": rest.split(':')[0],
                            "port": int(rest.split(':')[1])
                        })
                    elif len(line) > 50:
                        decoded = base64.b64decode(line).decode()
                        proxies.append(json.loads(decoded))
                except Exception as e:
                    logging.warning(f"Failed to parse line: {line}")
        return proxies

    def test_proxy(self, proxy: Dict) -> Optional[Dict]:
        """Test proxy availability, latency, and speed"""
        start_time = time.time()
        
        try:
            protocol = proxy.get("protocol", "").lower()
            server = proxy.get("server")
            port = proxy.get("port")
            
            if not all([protocol, server, port]):
                return None
                
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((server, int(port)))
            sock.close()
            
            if result != 0:
                return None
                
            latency = (time.time() - start_time) * 1000  # ms
            
            speed = self.test_speed(proxy)
            
            return {
                **proxy,
                "latency": latency,
                "speed": speed,
                "last_tested": time.time()
            }
            
        except Exception as e:
            logging.debug(f"Proxy test failed for {server}:{port}: {str(e)}")
            return None

    def test_speed(self, proxy: Dict) -> float:
        """Test proxy speed (simplified)"""
        protocol = proxy["protocol"].lower()
        test_url = "http://speedtest.google.com/test"
        
        try:
            if protocol in ["v2ray", "xray"]:
                temp_config = self.generate_temp_config(proxy)
                speed = self.measure_speed_with_singbox(temp_config, test_url)
            elif protocol == "wireguard":
                speed = self.measure_wireguard_speed(proxy)
            else:
                proxies = {protocol: f"{proxy['server']}:{proxy['port']}"}
                start = time.time()
                requests.get(test_url, proxies=proxies, timeout=self.timeout)
                speed = 1 / (time.time() - start)
                
            return speed
        except Exception:
            return 0

    def generate_temp_config(self, proxy: Dict) -> Dict:
        """Generate temporary Sing-Box config for testing"""
        return {
            "outbounds": [{
                "type": proxy["protocol"],
                "server": proxy["server"],
                "port": proxy["port"],
            }]
        }

    def measure_speed_with_singbox(self, config: Dict, url: str) -> float:
        """Measure speed using Sing-Box"""
        with open("temp_config.json", "w") as f:
            json.dump(config, f)
        
        start = time.time()
        try:
            subprocess.run(
                ["sing-box", "run", "-c", "temp_config.json"],
                timeout=self.timeout,
                capture_output=True
            )
            return 1 / (time.time() - start)
        except Exception:
            return 0
        finally:
            os.remove("temp_config.json")

    def measure_wireguard_speed(self, proxy: Dict) -> float:
        # Placeholder - implement WireGuard-specific speed test if needed
        return 0

    def select_best_proxies(self, proxies: List[Dict]) -> Dict[str, List[Dict]]:
        """Select best proxies by protocol"""
        tested_proxies = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            tested_proxies = list(filter(None, executor.map(self.test_proxy, proxies)))
        
        by_protocol = {}
        for proxy in tested_proxies:
            proto = proxy["protocol"].lower()
            if proto not in by_protocol:
                by_protocol[proto] = []
            by_protocol[proto].append(proxy)
        
        selected = {}
        for proto, plist in by_protocol.items():
            sorted_proxies = sorted(
                plist,
                key=lambda x: (x["latency"], -x["speed"])
            )
            selected[proto] = sorted_proxies[:self.max_proxies_per_type]
            
        return selected

    def update_singbox_config(self, proxies: Dict[str, List[Dict]]):
        """Update Sing-Box configuration file"""
        try:
            config = {
                "log": {"level": "info"},
                "outbounds": []
            }
            
            for protocol, plist in proxies.items():
                for proxy in plist:
                    outbound = {
                        "type": protocol,
                        "server": proxy["server"],
                        "port": proxy["port"],
                        "tag": f"{protocol}-{proxy['server']}"
                    }
                    if protocol == "wireguard":
                        outbound["private_key"] = proxy.get("private_key", "")
                    elif protocol in ["v2ray", "xray"]:
                        outbound["uuid"] = proxy.get("uuid", "")
                    
                    config["outbounds"].append(outbound)
            
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
            logging.info("Sing-Box configuration updated successfully")
            
        except Exception as e:
            logging.error(f"Failed to update config: {str(e)}")

    def run(self):
        """Main execution method"""
        logging.info("Starting proxy update cycle")
        proxies = self.fetch_proxies()
        if not proxies:
            logging.warning("No proxies fetched")
            return
            
        best_proxies = self.select_best_proxies(proxies)
        if best_proxies:
            self.update_singbox_config(best_proxies)
        else:
            logging.warning("No valid proxies found")

if __name__ == "__main__":
    manager = ProxyManager()
    manager.run()
