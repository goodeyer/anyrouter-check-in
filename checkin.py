#!/usr/bin/env python3
"""
多网站自动签到脚本 - 支持 AnyRouter.top 和其他基于相同开源系统的网站
"""

import os
import sys
import asyncio
import json
import time
import httpx
import re
from datetime import datetime
from typing import Union, List, Optional, Dict, Any
from playwright.async_api import async_playwright
from dotenv import load_dotenv
load_dotenv()

# 设置标准输出编码为 UTF-8
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
sys.stderr = codecs.getwriter('utf-8')(sys.stderr.detach())

from notify import NotificationKit

# 创建通知实例
notify = NotificationKit()

# 网站配置映射
SITE_CONFIGS = {
    "anyrouter.top": {
        "base_url": "https://anyrouter.top",
        "api_user_header": "new-api-user",
        "signin_endpoint": "/api/user/sign_in",
        "user_endpoint": "/api/user/self",
        "waf_login_url": "https://anyrouter.top/login"
    },
    "claude.husan97x.xyz": {
        "base_url": "https://claude.husan97x.xyz", 
        "api_user_header": "husan-api-user",
        "signin_endpoint": "/api/user/checkin",
        "user_endpoint": "/api/user/self",
        "waf_login_url": "https://claude.husan97x.xyz/login"
    }
}

def detect_site_type(account_info: Dict[str, Any]) -> str:
    """动态检测网站类型"""
    # 优先从配置中获取 site_type
    if "site_type" in account_info:
        site_type = account_info["site_type"]
        if site_type in SITE_CONFIGS:
            return site_type
        # 如果是域名格式，直接返回
        if site_type in [key.split(".")[0] for key in SITE_CONFIGS.keys()]:
            for domain in SITE_CONFIGS.keys():
                if site_type in domain:
                    return domain
    
    # 通过 api_user 数值特征推断（新网站 api_user 通常是短数字）
    api_user = account_info.get("api_user", "")
    if api_user and api_user.isdigit() and len(api_user) <= 4:
        return "claude.husan97x.xyz"
    
    # 默认返回原网站
    return "anyrouter.top"

def load_accounts():
    """从环境变量加载多账号配置"""
    accounts_str = os.getenv("ANYROUTER_ACCOUNTS")
    if not accounts_str:
        print("ERROR: ANYROUTER_ACCOUNTS environment variable not found")
        return None

    try:
        accounts_data = json.loads(accounts_str)

        # 检查是否为数组格式
        if not isinstance(accounts_data, list):
            print("ERROR: Account configuration must use array format [{}]")
            return None

        # 验证账号数据格式
        for i, account in enumerate(accounts_data):
            if not isinstance(account, dict):
                print(f"ERROR: Account {i+1} configuration format is incorrect")
                return None
            if "cookies" not in account or "api_user" not in account:
                print(f"ERROR: Account {i+1} missing required fields (cookies, api_user)")
                return None

        return accounts_data
    except Exception as e:
        print(f"ERROR: Account configuration format is incorrect: {e}")
        return None


def parse_cookies(cookies_data):
    """解析 cookies 数据"""
    if isinstance(cookies_data, dict):
        return cookies_data

    if isinstance(cookies_data, str):
        cookies_dict = {}
        for cookie in cookies_data.split(";"):
            if "=" in cookie:
                key, value = cookie.strip().split("=", 1)
                cookies_dict[key] = value
        return cookies_dict
    return {}


def format_message(message: Union[str, List[str]], use_emoji: bool = False) -> str:
    """格式化消息，支持 emoji 和纯文本"""
    emoji_map = {
        "success": "✅" if use_emoji else "[SUCCESS]",
        "fail": "❌" if use_emoji else "[FAILED]",
        "info": "ℹ️" if use_emoji else "[INFO]",
        "warn": "⚠️" if use_emoji else "[WARNING]",
        "error": "💥" if use_emoji else "[ERROR]",
        "money": "💰" if use_emoji else "[BALANCE]",
        "time": "⏰" if use_emoji else "[TIME]",
        "stats": "📊" if use_emoji else "[STATS]",
        "start": "🤖" if use_emoji else "[SYSTEM]",
        "loading": "🔄" if use_emoji else "[PROCESSING]",
        "trophy": "🏆" if use_emoji else "[TROPHY]"
    }
    
    if isinstance(message, str):
        result = message
        for key, value in emoji_map.items():
            result = result.replace(f":{key}:", value)
        # 确保字符串是 UTF-8 编码
        try:
            result = result.encode('utf-8', errors='ignore').decode('utf-8')
        except:
            pass
        return result
    elif isinstance(message, list):
        return "\n".join(format_message(m, use_emoji) for m in message if isinstance(m, str))
    return ""


async def get_waf_cookies_with_playwright(account_name: str, site_config: Dict[str, str]):
    """使用 Playwright 获取 WAF cookies（隐私模式）"""
    print(f"[PROCESSING] {account_name}: Starting browser to get WAF cookies...")
    
    async with async_playwright() as p:
        # 创建浏览器上下文（隐私模式）
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=None,  # 使用临时目录，相当于隐私模式
                headless=False,  # 有头模式运行
                # 如果需要指定 Chrome 路径，可以取消注释下面这行
                # executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--no-sandbox'  # 在 CI 环境中可能需要
                ]
            )
        except Exception as e:
            print(f"[FAILED] {account_name}: Failed to start headed mode, trying headless mode: {e}")
            # 如果有头模式失败，回退到无头模式
            context = await p.chromium.launch_persistent_context(
                user_data_dir=None,
                headless=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--no-sandbox'
                ]
            )
        
        # 创建页面
        page = await context.new_page()
        
        try:
            print(f"[PROCESSING] {account_name}: Step 1: Access login page to get initial cookies...")
            
            # 访问登录页面
            await page.goto(site_config["waf_login_url"], wait_until="networkidle")
            
            # 等待页面加载
            await page.wait_for_timeout(3000)
            
            # 获取当前 cookies
            cookies = await page.context.cookies()
            
            # 查找 WAF cookies
            waf_cookies = {}
            for cookie in cookies:
                if cookie['name'] in ['acw_tc', 'cdn_sec_tc', 'acw_sc__v2']:
                    waf_cookies[cookie['name']] = cookie['value']
            
            print(f"[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies after step 1")
            
            # 检查是否需要第二步
            if 'acw_sc__v2' not in waf_cookies:
                print(f"[PROCESSING] {account_name}: Step 2: Re-access page to get acw_sc__v2...")
                
                # 等待一段时间
                await page.wait_for_timeout(2000)
                
                # 刷新页面或重新访问
                await page.reload(wait_until="networkidle")
                
                # 等待页面加载
                await page.wait_for_timeout(3000)
                
                # 再次获取 cookies
                cookies = await page.context.cookies()
                
                # 更新 WAF cookies
                for cookie in cookies:
                    if cookie['name'] in ['acw_tc', 'cdn_sec_tc', 'acw_sc__v2']:
                        waf_cookies[cookie['name']] = cookie['value']
                
                print(f"[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies after step 2")
            
            # 验证是否获取到所有必要的 cookies
            required_cookies = ['acw_tc', 'cdn_sec_tc', 'acw_sc__v2']
            missing_cookies = [c for c in required_cookies if c not in waf_cookies]
            
            if missing_cookies:
                print(f"[FAILED] {account_name}: Missing WAF cookies: {missing_cookies}")
                await context.close()
                return None
            
            print(f"[SUCCESS] {account_name}: Successfully got all WAF cookies")
            
            # 关闭浏览器上下文
            await context.close()
            
            return waf_cookies
            
        except Exception as e:
            print(f"[FAILED] {account_name}: Error occurred while getting WAF cookies: {e}")
            await context.close()
            return None


def get_user_info(client, headers, site_config: Dict[str, str]):
    """获取用户信息"""
    try:
        user_endpoint = site_config["base_url"] + site_config["user_endpoint"]
        response = client.get(
            user_endpoint,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                user_data = data.get("data", {})
                quota = round(user_data.get("quota", 0) / 500000, 2)
                used_quota = round(user_data.get("used_quota", 0) / 500000, 2)
                return f":money: Current balance: ${quota}, Used: ${used_quota}"
    except Exception as e:
        return f":fail: Failed to get user info: {str(e)[:50]}..."
    return None


async def check_in_account(account_info, account_index):
    """为单个账号执行签到操作"""
    account_name = f"Account {account_index + 1}"
    print(f"\n[PROCESSING] Starting to process {account_name}")

    # 解析账号配置
    cookies_data = account_info.get("cookies", {})
    api_user = account_info.get("api_user", "")

    if not api_user:
        print(f"[FAILED] {account_name}: API user identifier not found")
        return False, None

    # 解析用户 cookies
    user_cookies = parse_cookies(cookies_data)
    if not user_cookies:
        print(f"[FAILED] {account_name}: Invalid configuration format")
        return False, None

    # 动态检测网站类型
    site_type = detect_site_type(account_info)
    site_config = SITE_CONFIGS.get(site_type, SITE_CONFIGS["anyrouter.top"])
    print(f"[INFO] {account_name}: Detected site type: {site_type}")

    # 步骤1：根据网站类型获取 cookies
    if "husan97x.xyz" in site_config["base_url"]:
        # 新网站：直接使用 session，无需 WAF cookies
        print(f"[INFO] {account_name}: Using direct session for WeChat site")
        all_cookies = user_cookies
    else:
        # 传统网站：需要 WAF cookies
        waf_cookies = await get_waf_cookies_with_playwright(account_name, site_config)
        if not waf_cookies:
            print(f"[FAILED] {account_name}: Unable to get WAF cookies")
            return False, None
        all_cookies = {**waf_cookies, **user_cookies}

    # 步骤2：使用 httpx 进行 API 请求
    client = httpx.Client(http2=True, timeout=30.0)
    
    try:
        # 设置 cookies
        client.cookies.update(all_cookies)

        # 设置请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": f"{site_config['base_url']}/console",
            "Origin": site_config["base_url"],
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            site_config["api_user_header"]: api_user,
        }

        user_info_text = None
        
        # 获取用户信息
        user_info_before = get_user_info(client, headers, site_config)
        if user_info_before:
            print(user_info_before)
            user_info_text = user_info_before

        # 执行签到操作
        print(f"[NETWORK] {account_name}: Executing check-in")
        
        # 更新签到请求头
        checkin_headers = headers.copy()
        checkin_headers.update({
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        })
        
        signin_url = site_config["base_url"] + site_config["signin_endpoint"]
        response = client.post(
            signin_url,
            headers=checkin_headers,
            timeout=30
        )
        
        print(f"[RESPONSE] {account_name}: Response status code {response.status_code}")

        if response.status_code == 200:
            try:
                result = response.json()
                if (
                    result.get("ret") == 1
                    or result.get("code") == 0
                    or result.get("success")
                ):
                    # 直接从API响应中提取签到积分
                    reward_amount = 0
                    reward_info = ""
                    
                    # 优先从 reward_dollar 字段获取
                    if "reward_dollar" in result:
                        reward_amount = result["reward_dollar"]
                    elif "data" in result and "reward_dollar" in result["data"]:
                        reward_amount = result["data"]["reward_dollar"]
                    elif "reward" in result:
                        # 如果 reward 是以分为单位，转换为美元
                        reward = result["reward"]
                        if reward > 1000:  # 假设大于1000是以分为单位
                            reward_amount = reward // 1000000  # 转换为美元
                        else:
                            reward_amount = reward
                    
                    # 如果有奖励信息，添加到用户信息中
                    if reward_amount > 0:
                        reward_text = f"[REWARD] Check-in reward: +${reward_amount}"
                        if user_info_text:
                            user_info_text = f"{user_info_text}\n{reward_text}"
                        else:
                            user_info_text = reward_text
                        
                        reward_info = f" (+${reward_amount})"
                    
                    print(f"[SUCCESS] {account_name}: Check-in successful{reward_info}!")
                    return True, user_info_text
                else:
                    error_msg = result.get("msg", result.get("message", "Unknown error"))
                    # 检查是否为"已经签到"的成功提示
                    if any(keyword in error_msg for keyword in ["已经签到", "已经签到了", "不要太贪心", "签到过了"]):
                        # 尝试从错误消息中提取积分信息
                        reward_amount = 0
                        import re
                        # 查找类似 "获得3积分" 或 "签到成功+3" 等格式
                        reward_match = re.search(r'(\d+)\s*积分', error_msg)
                        if not reward_match:
                            reward_match = re.search(r'[+＋]\s*(\d+)', error_msg)
                        if not reward_match:
                            reward_match = re.search(r'(\d+)', error_msg)
                        
                        if reward_match:
                            reward_amount = int(reward_match.group(1))
                            reward_text = f"[REWARD] Today's check-in reward: +${reward_amount}"
                            if user_info_text:
                                user_info_text = f"{user_info_text}\n{reward_text}"
                            else:
                                user_info_text = reward_text
                        else:
                            # 如果无法提取，显示为0
                            reward_text = f"[REWARD] Today's check-in reward: +$0"
                            if user_info_text:
                                user_info_text = f"{user_info_text}\n{reward_text}"
                            else:
                                user_info_text = reward_text
                        
                        print(f"[SUCCESS] {account_name}: Already checked in today - {error_msg}")
                        return True, user_info_text
                    else:
                        print(f"[FAILED] {account_name}: Check-in failed - {error_msg}")
                        return False, user_info_text
            except json.JSONDecodeError:
                # 如果不是 JSON 响应，检查是否包含成功标识
                response_text = response.text
                if "success" in response_text.lower():
                    print(f"[SUCCESS] {account_name}: Check-in successful!")
                    return True, user_info_text
                # 检查中文签到成功提示
                elif any(keyword in response_text for keyword in ["已经签到", "已经签到了", "不要太贪心", "签到过了"]):
                    print(f"[SUCCESS] {account_name}: Already checked in today (Chinese message)")
                    return True, user_info_text
                else:
                    print(f"[FAILED] {account_name}: Check-in failed - Invalid response format")
                    return False, user_info_text
        else:
            print(f"[FAILED] {account_name}: Check-in failed - HTTP {response.status_code}")
            return False, user_info_text

    except Exception as e:
        print(f"[FAILED] {account_name}: Error occurred during check-in process - {str(e)[:50]}...")
        return False, user_info_text
    finally:
        # 关闭 HTTP 客户端
        client.close()


async def main():
    """主函数"""
    print(f"[SYSTEM] Multi-site auto check-in script started (using Playwright)")
    print(f"[TIME] Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 加载账号配置
    accounts = load_accounts()
    if not accounts:
        print("[FAILED] Unable to load account configuration, program exits")
        sys.exit(1)

    print(f"[INFO] Found {len(accounts)} account configurations")

    # 为每个账号执行签到
    success_count = 0
    total_count = len(accounts)
    notification_content = []

    for i, account in enumerate(accounts):
        try:
            success, user_info = await check_in_account(account, i)
            if success:
                success_count += 1
            # 收集通知内容
            status = ":success:" if success else ":fail:"
            account_result = f"{status} Account {i+1}"
            if user_info:
                account_result += f"\n{user_info}"
            notification_content.append(account_result)
        except Exception as e:
            print(f"[FAILED] Account {i+1} processing exception: {e}")
            notification_content.append(f":fail: Account {i+1} exception: {str(e)[:50]}...")

    # 构建通知内容
    summary = [
        ":stats: Check-in result statistics:",
        f":success: Success: {success_count}/{total_count}",
        f":fail: Failed: {total_count - success_count}/{total_count}"
    ]

    if success_count == total_count:
        summary.append(":success: All accounts check-in successful!")
    elif success_count > 0:
        summary.append(":warn: Some accounts check-in successful")
    else:
        summary.append(":error: All accounts check-in failed")
    
    # 添加详细统计信息
    summary.append("")
    summary.append("Account Check-in Rewards:")
    
    # 添加每个账号的签到积分
    for i, account_result in enumerate(notification_content):
        account_lines = account_result.split('\n')
        
        # 查找签到奖励信息
        reward_text = ""
        for line in account_lines[1:]:
            if "[REWARD]" in line and ("Check-in reward" in line or "Today's check-in reward" in line):
                import re
                reward_match = re.search(r'\+\$(\d+)', line)
                if reward_match:
                    reward_text = f"Account {i+1}:+{reward_match.group(1)}"
                    break
        
        # 如果没有找到奖励信息，显示0
        if not reward_text:
            reward_text = f"Account {i+1}:+0"
        
        summary.append(reward_text)

    # 生成通知内容
    time_info = f":time: Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    # 控制台输出
    console_content = "\n".join([
        format_message(time_info, use_emoji=False),
        format_message(notification_content, use_emoji=False),
        format_message(summary, use_emoji=False)
    ])
    
    # 通知内容
    notify_content = "\n\n".join([
        format_message(time_info),
        format_message(notification_content),
        format_message(summary)
    ])

    # 输出到控制台
    print("\n" + console_content)
    
    # 发送通知
    notify.push_message("AnyRouter Check-in Results", notify_content, msg_type='text')

    # 设置退出码
    sys.exit(0 if success_count > 0 else 1)


def run_main():
    """运行主函数的包装函数"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[WARNING] Program interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAILED] Error occurred during program execution: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_main()
