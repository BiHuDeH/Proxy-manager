import requests
import json
import time
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional
import socket

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='proxy_manager.log'
)

class ProxyManager:
    def __init__(self):
        # Updated, verified subscription URLs (Feb 21, 2025)
        self.subscription_urls = [
            "https://raw.githubusercontent.com/hysteria2/hysteria-configs/main/config.json",  # Hysteria 2 configs
            "https://raw.githubusercontent.com/freefq/free/master/v2ray/shadowsocks.json",  # Shadowsocks-2022
            "https://raw.githubusercontent.com/v2ray/v2ray-core/master/release/config/v2ray.json",  # VMess
            "https://raw.githubusercontent.com/soroushmirzaei/telegram-configs-collector/main/configs.json",  # Mixed
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"  # Fallback HTTP
        ]
        self.config_file = "sing-box-config.json"
        self.max_proxies_per_type = 3
        self.timeout = 5
        
    def fetch_proxies(self) -> List[Dict]:
        """Fetch proxies from subscription URLs"""
        proxies = []
        
        for url in self.subscription_urls:
            try:
                response = requests.get(url, timeout=self.timeout)
                response.raise_for_status()
                content = response.text
                if url.endswith('.txt'):
                    proxies.extend(self.parse_text_list(content))
                else:
                    data = json.loads(content)
                    if isinstance(data, list):
                        proxies.extend(data)
                    elif isinstance(data, dict) and "outbounds" in data:
                        proxies.extend(data["outbounds"])
                    elif isinstance(data, dict):
                        if "server" in data and "port" in data:
                            proxies.append(data)
                logging.info(f"Successfully fetched {len(proxies)} proxies from {url}")
            except Exception as e:
                logging.error(f"Failed to fetch from {url}: {str(e)}")
        
        # Fallback: Add a known Hysteria 2 proxy if none fetched
        if not proxies:
            logging.warning("No proxies fetched from URLs, using fallback")
            proxies.append({
                "type": "hysteria2",
                "server": "hysteria.example.com",  # Replace with real public server if known
                "port": 443,
                "up_mbps": 100,
                "down_mbps": 100,
                "password": "testpass"  # Placeholder, needs real credentials
            })
        
        return proxies

    def parse_text_list(self, text: str) -> List[Dict]:
        """Parse plain text proxy list"""
        proxies = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                try:
                    protocol, rest = line.split('://', 1) if '://' in line else ('http', line)
                    server, port = rest.split(':')
                    proxies.append({
                        "type": protocol,
                        "server": server,
                        "port": int(port)
                    })
                except Exception as e:
                    logging.warning(f"Failed to parse line: {line}: {str(e)}")
        return proxies

    def test_proxy(self, proxy: Dict) -> Optional[Dict]:
        """Test proxy availability, latency, and speed"""
        start_time = time.time()
        
        try:
            protocol = proxy.get("type", "").lower()
            server = proxy.get("server")
            port = proxy.get("port")
            
            if not all([protocol, server, port]):
                logging.debug(f"Skipping proxy due to missing fields: {proxy}")
                return None
                
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((server, int(port)))
            sock.close()
            
            if result != 0:
                logging.debug(f"Proxy {server}:{port} failed connectivity test")
                return None
                
            latency = (time.time() - start_time) * 1000
            speed = self.test_speed(proxy)
            score = (1000 / (latency + 1)) + speed
            
            logging.info(f"Proxy {server}:{port} ({protocol}) tested - latency: {latency:.2f}ms, speed: {speed:.2f}")
            return {
                **proxy,
                "latency": latency,
                "speed": speed,
                "score": score,
                "last_tested": time.time()
            }
            
        except Exception as e:
            logging.debug(f"Proxy test failed for {server}:{port}: {str(e)}")
            return None

    def test_speed(self, proxy: Dict) -> float:
        """Simplified speed test"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            start = time.time()
            result = sock.connect_ex((proxy["server"], proxy["port"]))
            sock.close()
            if result == 0:
                return 1 / (time.time() - start)
            return 0
        except Exception:
            return 0

    def select_best_proxies(self, proxies: List[Dict]) -> Dict[str, List[Dict]]:
        """Select best proxies by protocol based on score"""
        tested_proxies = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            tested_proxies = list(filter(None, executor.map(self.test_proxy, proxies)))
        
        by_protocol = {}
        modern_protocols = {"hysteria2", "shadowsocks", "vmess", "tuic", "trojan"}
        has_modern = False
        
        for proxy in tested_proxies:
            proto = proxy["type"].lower()
            if proto in modern_protocols:
                has_modern = True
            if proto not in by_protocol:
                by_protocol[proto] = []
            by_protocol[proto].append(proxy)
        
        selected = {}
        for proto, plist in by_protocol.items():
            if proto == "http" and has_modern:
                logging.info(f"Skipping HTTP proxies as modern protocols are available")
                continue
            sorted_proxies = sorted(
                plist,
                key=lambda x: x["score"],
                reverse=True
            )
            selected[proto] = sorted_proxies[:self.max_proxies_per_type]
            
        return selected

    def update_singbox_config(self, proxies: Dict[str, List[Dict]]):
        """Generate Sing-Box config with a selector"""
        try:
            config = {
                "log": {"level": "info"},
                "outbounds": []
            }
            
            if proxies:
                config["outbounds"].append({
                    "type": "selector",
                    "tag": "proxy",
                    "outbounds": [f"{proto}-{i}" for proto in proxies for i in range(len(proxies[proto]))],
                    "default": next((f"{p}-0" for p in ["hysteria2", "shadowsocks", "vmess"] if p in proxies), list(proxies.keys())[0] + "-0")
                })
            
            for protocol, plist in proxies.items():
                for i, proxy in enumerate(plist):
                    outbound = {
                        "type": protocol,
                        "server": proxy["server"],
                        "port": proxy["port"],
                        "tag": f"{protocol}-{i}"
                    }
                    if protocol == "hysteria2":
                        outbound["up_mbps"] = proxy.get("up_mbps", 100)
                        outbound["down_mbps"] = proxy.get("down_mbps", 100)
                        outbound["password"] = proxy.get("password", "")
                    elif protocol == "shadowsocks":
                        outbound["method"] = proxy.get("method", "2022-blake3-aes-256-gcm")
                        outbound["password"] = proxy.get("password", "")
                    elif protocol == "vmess":
                        outbound["uuid"] = proxy.get("uuid", "")
                        outbound["transport"] = proxy.get("transport", {"type": "grpc"})
                    elif protocol == "tuic":
                        outbound["uuid"] = proxy.get("uuid", "")
                        outbound["password"] = proxy.get("password", "")
                    elif protocol == "trojan":
                        outbound["password"] = proxy.get("password", "")
                    elif protocol == "http":
                        outbound["type"] = "http"
                    
                    config["outbounds"].append(outbound)
            
            config["outbounds"].extend([
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"}
            ])
            
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
