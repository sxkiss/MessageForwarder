import os
import tomli # 使用 tomli 库，兼容 Python 3.6+
import base64
import tempfile
import re # 导入 re for regex
from pathlib import Path
import xml.etree.ElementTree as ET # 导入 XML 解析库
import shutil # 新增导入 shutil 用于文件操作
import asyncio # 新增导入 asyncio 用于异步操作
import time # 新增导入 time 用于时间戳
from typing import Optional # 新增导入 Optional
import json # 新增导入 json 用于解析 ffprobe 输出
import aiohttp # 新增导入 aiohttp 用于直接API调用

from loguru import logger

from utils.plugin_base import PluginBase
from WechatAPI import WechatAPIClient
from utils.decorators import (
    on_text_message,
    on_image_message,
    on_video_message,
    on_xml_message,
    on_other_message,
)


class MessageForwarder(PluginBase):
    description = "消息转发插件"
    author = "sxkiss"
    version = "1.1.0"

    def __init__(self):
        super().__init__()
        self.target_wxid = None
        self.target_type = None
        self.listen_type = "all"
        self.listen_user_wxids = []
        self.listen_group_wxids = []
        # 初始化临时目录
        self.temp_dir = Path(__file__).parent / "temp"
        self._ensure_temp_dir()
        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        if os.path.exists(config_path):
            with open(config_path, "rb") as f:
                config = tomli.load(f)

            forwarder_config = config.get("forwarder", {})
            self.target_type = forwarder_config.get("target_type", "user")
            if self.target_type == "user":
                self.target_wxid = forwarder_config.get("target_user_wxid")
            elif self.target_type == "group":
                self.target_wxid = forwarder_config.get("target_group_wxid")
            logger.info(f"消息转发插件配置加载成功，目标类型: {self.target_type}, 目标WXID: {self.target_wxid}")

            listen_source_config = config.get("listen_source", {})
            self.listen_type = listen_source_config.get("listen_type", "all")
            self.listen_user_wxids = listen_source_config.get("listen_user_wxids", [])
            self.listen_group_wxids = listen_source_config.get("listen_group_wxids", [])
            logger.info(f"消息转发插件监听源配置加载成功，监听类型: {self.listen_type}, 监听用户WXID: {self.listen_user_wxids}, 监听群聊WXID: {self.listen_group_wxids}")
        else:
            logger.warning("消息转发插件配置文件 config.toml 不存在，请检查配置。")

    def _ensure_temp_dir(self):
        """确保临时目录存在"""
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[MessageForwarder] 创建临时目录失败: {e}")
            # 如果无法创建指定目录，尝试使用系统临时目录
            import tempfile
            self.temp_dir = Path(tempfile.gettempdir()) / "MessageForwarder_temp"
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            logger.warning(f"[MessageForwarder] 使用备用临时目录: {self.temp_dir}")

    async def on_enable(self, bot=None):
        """插件启用时调用，重新加载配置"""
        self._load_config()
        await super().on_enable(bot)

    def _is_message_allowed(self, message: dict) -> bool:
        """检查消息是否符合监听条件"""
        from_wxid = message.get("FromWxid")
        sender_wxid = message.get("SenderWxid") # 实际发送消息的用户 wxid (如果是群聊)
        room_wxid = message.get("FromWxid") if message.get("IsGroup") else None # 群聊 wxid (如果是群聊)
        message_type = message.get("MsgType") # 消息类型

        logger.debug(f"检查消息: from_wxid={from_wxid}, sender_wxid={sender_wxid}, room_wxid={room_wxid}, msg_type={message_type}")
        logger.debug(f"监听配置: listen_type={self.listen_type}, listen_user_wxids={self.listen_user_wxids}, listen_group_wxids={self.listen_group_wxids}")

        if self.listen_type == "all":
            logger.debug("监听类型为 'all'，允许转发。")
            return True
        elif self.listen_type == "user":
            # 如果是私聊，from_wxid 是发送者 wxid
            # 如果是群聊，sender_wxid 是实际发送者 wxid
            is_allowed = from_wxid in self.listen_user_wxids or sender_wxid in self.listen_user_wxids
            logger.debug(f"监听类型为 'user'，from_wxid是否在列表: {from_wxid in self.listen_user_wxids}, sender_wxid是否在列表: {sender_wxid in self.listen_user_wxids}，结果: {is_allowed}")
            return is_allowed
        elif self.listen_type == "group":
            # 如果是群聊，room_wxid 是群聊 wxid
            is_allowed = room_wxid in self.listen_group_wxids
            logger.debug(f"监听类型为 'group'，room_wxid是否在列表: {room_wxid in self.listen_group_wxids}，结果: {is_allowed}")
            return is_allowed
        logger.debug("未知监听类型，拒绝转发。")
        return False

    async def _forward_message(self, bot: WechatAPIClient, message: dict, forward_func, *args, **kwargs):
        """通用消息转发方法"""
        if not self._is_message_allowed(message):
            logger.debug(f"消息来自未监听的源，跳过转发: {message.get('from_wxid')}")
            return

        if not self.target_wxid:
            logger.warning("未配置转发目标WXID，无法转发消息。")
            return

        try:
            await forward_func(self.target_wxid, *args, **kwargs)
            # 确保msg_type在日志中安全显示，避免NoneType错误
            msg_type_display = message.get('MsgType', '未知类型')
            from_wxid_display = message.get('FromWxid', '未知来源')
            logger.info(f"成功转发消息: {msg_type_display} from {from_wxid_display} to {self.target_wxid}")
        except Exception as e:
            logger.error(f"转发消息失败: {e}")

    async def _save_base64_to_file(self, base64_data: str, file_extension: str = ".mp4") -> Optional[Path]:
        """将Base64数据保存为临时文件"""
        try:
            decoded_data = base64.b64decode(base64_data)
            temp_filepath = self.temp_dir / f"temp_{int(time.time())}{file_extension}"
            with open(temp_filepath, "wb") as f:
                f.write(decoded_data)
            logger.debug(f"Base64数据成功保存到临时文件: {temp_filepath}")
            return temp_filepath
        except Exception as e:
            logger.error(f"保存Base64数据到文件失败: {e}")
            return None

    async def _extract_first_frame_from_video(self, video_path: Path) -> Optional[str]:
        """从本地视频文件提取第一帧并返回base64字符串"""
        thumbnail_path = None
        try:
            thumbnail_path = self.temp_dir / f"temp_thumbnail_{int(time.time())}.jpg"
            
            # 使用 subprocess.run，参考 VideoDemand 的实现
            import subprocess
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', str(video_path),
                '-ss', '00:00:01',
                '-vframes', '1',
                str(thumbnail_path),
                '-y'
            ]
            
            logger.debug(f"执行ffmpeg命令: {' '.join(ffmpeg_cmd)}")
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg 提取封面失败 (返回码: {result.returncode}): {result.stderr}")
                logger.debug(f"ffmpeg stdout: {result.stdout}")
                return None
            else:
                logger.debug(f"ffmpeg 提取封面成功")

            # 读取生成的缩略图并转换为base64
            if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
                with open(thumbnail_path, "rb") as image_file:
                    image_data = image_file.read()
                    image_base64 = base64.b64encode(image_data).decode("utf-8")
                    logger.info(f"成功生成视频缩略图，大小: {len(image_data)} 字节")
                    return image_base64
            else:
                logger.error(f"缩略图文件不存在或为空: {thumbnail_path}")
                return None

        except Exception as e:
            logger.error(f"提取视频首帧失败: {video_path} - {e}")
            return None
        finally:
            # 清理临时缩略图文件
            if thumbnail_path and thumbnail_path.exists():
                try:
                    thumbnail_path.unlink()
                    logger.debug(f"清理临时缩略图文件: {thumbnail_path}")
                except Exception as cleanup_error:
                    logger.error(f"清理临时缩略图文件失败: {cleanup_error}")


    @on_text_message(priority=10)
    async def handle_text_message(self, bot: WechatAPIClient, message: dict):
        """处理文本消息并转发"""
        content = message.get("Content")
        if content:
            await self._forward_message(bot, message, bot.send_text_message, content)

    @on_image_message(priority=10)
    async def handle_image_message(self, bot: WechatAPIClient, message: dict):
        """处理图片消息并转发 (使用Base64)"""
        # 根据实际消息结构，图片base64数据可能在 'Content' 或 'Image'字段
        # 优先使用 'Image'字段，如果不存在则尝试 'Content'
        base64_data = message.get("Image") or message.get("Content")

        if base64_data:
            try:
                # 使用 send_image_message 发送Base64图片内容
                await self._forward_message(bot, message, bot.send_image_message, base64_data)
                logger.success("图片消息转发成功 (使用Base64)")
            except Exception as e:
                logger.error(f"处理图片消息失败: {e}")
        else:
            logger.warning("图片消息缺少Base64内容，无法转发。")


    @on_video_message(priority=10)
    async def handle_video_message(self, bot: WechatAPIClient, message: dict):
        """处理视频消息并转发"""
        # 首先尝试使用 CDN 转发方法（如果消息包含 XML 内容）
        if await self._try_cdn_video_forward(bot, message):
            return
        
        # 如果 CDN 转发失败，使用传统的 base64 转发方法
        await self._handle_video_with_base64(bot, message)

    async def _try_cdn_video_forward(self, bot: WechatAPIClient, message: dict) -> bool:
        """尝试使用 CDN 方式转发视频消息"""
        try:
            # 检查消息是否包含 XML 内容（CDN 视频消息的特征）
            xml_content = message.get("Xml") or message.get("Content")
            if xml_content and "<msg>" in xml_content and "cdnvideourl" in xml_content.lower():
                logger.info("检测到 CDN 视频消息，尝试直接转发")
                await self._forward_message(
                    bot,
                    message,
                    bot.send_cdn_video_msg,
                    xml=xml_content
                )
                logger.success("CDN 视频消息转发成功")
                return True
        except Exception as e:
            logger.warning(f"CDN 视频转发失败，将使用备用方法: {e}")
        
        return False

    async def _handle_video_with_base64(self, bot: WechatAPIClient, message: dict):
        """使用 base64 方式处理视频消息转发"""
        # 根据实际消息结构，视频base64数据可能在 'Content' 或 'Video' 字段
        video_base64_data = message.get("Video") or message.get("Content")

        if not video_base64_data:
            logger.warning("视频消息缺少Base64内容，无法转发。")
            return

        temp_video_path = None
        
        try:
            logger.info(f"开始处理视频消息，Base64数据长度: {len(video_base64_data)}")

            # 1. 将Base64视频数据保存到临时文件
            temp_video_path = await self._save_base64_to_file(video_base64_data, file_extension=".mp4")
            if not temp_video_path or not temp_video_path.exists():
                logger.error("保存视频Base64数据到临时文件失败")
                return
            
            logger.info(f"视频文件已保存到: {temp_video_path}")

            # 2. 生成视频缩略图
            thumb_data = await self._extract_first_frame_from_video(temp_video_path)
            
            # 3. 转发视频消息，参考 VideoDemand 的实现
            if thumb_data:
                # 有缩略图，发送带缩略图的视频消息
                await self._forward_message(
                    bot,
                    message,
                    bot.send_video_message,
                    video=video_base64_data,  # 使用原始视频数据
                    image=thumb_data  # 传递base64缩略图数据
                )
                logger.success("视频消息转发成功 (带缩略图)")
            else:
                # 没有缩略图，使用默认方式发送
                await self._forward_message(
                    bot,
                    message,
                    bot.send_video_message,
                    video=video_base64_data,
                    image="None"  # 使用字符串"None"与VideoDemand保持一致
                )
                logger.success("视频消息转发成功 (无缩略图)")

        except Exception as e:
            logger.error(f"处理视频消息失败: {e}")
        finally:
            # 清理临时视频文件
            if temp_video_path and temp_video_path.exists():
                try:
                    temp_video_path.unlink()
                    logger.debug(f"清理临时视频文件: {temp_video_path}")
                except Exception as cleanup_error:
                    logger.error(f"清理临时视频文件失败: {cleanup_error}")

    @on_other_message(priority=10)
    async def handle_other_message(self, bot: WechatAPIClient, message: dict):
        """处理其他类型消息，包括名片消息"""
        msg_type = message.get("MsgType")
        
        # 检查是否为名片消息 (MsgType=42)
        if msg_type == 42:
            logger.info(f"[MessageForwarder] 检测到名片消息: MsgType={msg_type}")
            # 先预处理名片消息，设置正确的FromWxid和SenderWxid字段
            self._preprocess_card_message(message)
            await self.handle_card_message(bot, message)
        else:
            logger.debug(f"[MessageForwarder] 收到其他类型消息: MsgType={msg_type}")

    async def handle_card_message(self, bot: WechatAPIClient, message: dict):
        """处理名片消息并转发"""
        # 获取XML内容
        xml_content = message.get("Content", {})
        if isinstance(xml_content, dict):
            xml_string = xml_content.get("string", "")
        else:
            xml_string = str(xml_content)

        if not xml_string:
            logger.warning("[MessageForwarder] 名片消息缺少XML内容，无法转发。")
            return

        logger.debug(f"[MessageForwarder] 名片XML内容: {xml_string[:200]}...")

        try:
            # 解析XML内容提取名片信息
            card_info = self._parse_card_xml(xml_string)
            if not card_info:
                logger.warning("[MessageForwarder] 无法解析名片XML内容，跳过转发。")
                return

            # 使用直接API调用转发名片消息
            await self._forward_card_message_direct(
                bot,
                message,
                card_info["wxid"],
                card_info["nickname"],
                card_info.get("alias", "")
            )
            logger.success(f"[MessageForwarder] 名片消息转发成功: {card_info['nickname']} ({card_info['wxid']})")

        except Exception as e:
            logger.error(f"[MessageForwarder] 处理名片消息失败: {e}")

    async def _forward_card_message_direct(self, bot: WechatAPIClient, message: dict, card_wxid: str, card_nickname: str, card_alias: str = ""):
        """直接转发名片消息的专用方法"""
        if not self._is_message_allowed(message):
            logger.debug(f"消息来自未监听的源，跳过转发: {message.get('FromWxid')}")
            return

        if not self.target_wxid:
            logger.warning("未配置转发目标WXID，无法转发消息。")
            return

        try:
            await self._send_share_card_direct(bot, self.target_wxid, card_wxid, card_nickname, card_alias)
            logger.info(f"成功转发名片消息: {card_nickname} ({card_wxid}) from {message.get('FromWxid')} to {self.target_wxid}")
        except Exception as e:
            logger.error(f"转发名片消息失败: {e}")

    async def _send_share_card_direct(self, bot: WechatAPIClient, wxid: str, card_wxid: str, card_nickname: str, card_alias: str = ""):
        """直接调用ShareCard API发送名片消息"""
        if not bot.wxid:
            raise Exception("Bot未登录")

        async with aiohttp.ClientSession() as session:
            json_param = {
                "Wxid": bot.wxid,
                "ToWxid": wxid,
                "CardWxId": card_wxid,
                "CardNickName": card_nickname,
                "CardAlias": card_alias
            }
            
            api_url = f'http://{bot.ip}:{bot.port}/api/Msg/ShareCard'
            logger.debug(f"[MessageForwarder] 调用ShareCard API: {api_url}")
            logger.debug(f"[MessageForwarder] 请求参数: {json_param}")
            
            response = await session.post(api_url, json=json_param)
            json_resp = await response.json()

            if json_resp.get("Success"):
                logger.info(f"[MessageForwarder] ShareCard API调用成功: 对方wxid:{wxid} 名片wxid:{card_wxid} 名片昵称:{card_nickname}")
                return json_resp
            else:
                error_msg = json_resp.get("Message", "未知错误")
                logger.error(f"[MessageForwarder] ShareCard API调用失败: {error_msg}")
                raise Exception(f"ShareCard API调用失败: {error_msg}")

    def _preprocess_card_message(self, message: dict):
        """预处理名片消息，设置正确的FromWxid和SenderWxid字段"""
        logger.debug(f"[MessageForwarder] 名片消息预处理开始，原始消息字段: {list(message.keys())}")
        
        # 处理FromWxid字段
        if "FromWxid" not in message:
            # 如果没有FromWxid，尝试从FromUserName提取
            from_user = message.get("FromUserName", {})
            if isinstance(from_user, dict):
                message["FromWxid"] = from_user.get("string", "")
            else:
                message["FromWxid"] = str(from_user) if from_user else ""
        
        # 确保FromWxid是字符串
        if not isinstance(message.get("FromWxid"), str):
            message["FromWxid"] = str(message.get("FromWxid", ""))

        # 处理ToWxid字段
        if "ToWxid" not in message:
            # 如果没有ToWxid，尝试从ToUserName提取
            to_user = message.get("ToUserName", {})
            if isinstance(to_user, dict):
                message["ToWxid"] = to_user.get("string", "")
            else:
                message["ToWxid"] = str(to_user) if to_user else ""
        
        # 确保ToWxid是字符串
        if not isinstance(message.get("ToWxid"), str):
            message["ToWxid"] = str(message.get("ToWxid", ""))

        # 检查是否为群聊消息
        from_wxid = message["FromWxid"]
        if from_wxid.endswith("@chatroom"):
            message["IsGroup"] = True
            # 对于群聊中的名片消息，需要从Content中提取实际发送者
            xml_content = message.get("Content", {})
            if isinstance(xml_content, dict):
                xml_string = xml_content.get("string", "")
            else:
                xml_string = str(xml_content)
            
            # 尝试从XML前缀中提取发送者wxid（格式：发送者id:\n<xml...>）
            if ":\n" in xml_string:
                lines = xml_string.split(":\n", 1)
                if len(lines) > 1:
                    sender_wxid = lines[0].strip()
                    message["SenderWxid"] = sender_wxid
                    logger.debug(f"[MessageForwarder] 群聊名片消息，发送者: {sender_wxid}")
                else:
                    message["SenderWxid"] = from_wxid
            else:
                message["SenderWxid"] = from_wxid
        else:
            message["IsGroup"] = False
            message["SenderWxid"] = from_wxid

        logger.debug(f"[MessageForwarder] 名片消息预处理完成: FromWxid={message['FromWxid']}, ToWxid={message['ToWxid']}, SenderWxid={message['SenderWxid']}, IsGroup={message.get('IsGroup', False)}")

    def _parse_card_xml(self, xml_string: str) -> Optional[dict]:
        """解析名片XML内容，提取关键信息"""
        try:
            logger.debug(f"[MessageForwarder] 开始解析名片XML: {xml_string[:100]}...")
            
            # 移除XML前缀（如果存在，格式：发送者id:\n<xml...>）
            if ":\n" in xml_string:
                xml_string = xml_string.split(":\n", 1)[1].strip()
                logger.debug(f"[MessageForwarder] 移除前缀后的XML: {xml_string[:100]}...")
            
            # 处理可能的前缀格式（如：sxkiss_com:\n<xml...>）
            if xml_string.startswith(xml_string.split(":", 1)[0] + ":") and ":" in xml_string:
                parts = xml_string.split(":", 1)
                if len(parts) > 1 and parts[1].strip().startswith("<?xml") or parts[1].strip().startswith("<msg"):
                    xml_string = parts[1].strip()
                    logger.debug(f"[MessageForwarder] 移除前缀后的XML: {xml_string[:100]}...")

            # 解析XML
            root = ET.fromstring(xml_string)
            
            # 提取名片信息
            card_info = {
                "wxid": root.get("username", ""),
                "nickname": root.get("nickname", ""),
                "alias": root.get("alias", "")
            }

            # 验证必需字段
            if not card_info["wxid"] or not card_info["nickname"]:
                logger.warning(f"[MessageForwarder] 名片信息不完整: wxid={card_info['wxid']}, nickname={card_info['nickname']}")
                return None

            logger.debug(f"[MessageForwarder] 解析名片信息成功: {card_info}")
            return card_info

        except ET.ParseError as e:
            logger.error(f"[MessageForwarder] XML解析失败: {e}")
            logger.debug(f"[MessageForwarder] 失败的XML内容: {xml_string}")
            return None
        except Exception as e:
            logger.error(f"[MessageForwarder] 解析名片XML时发生未知错误: {e}")
            logger.debug(f"[MessageForwarder] 失败的XML内容: {xml_string}")
            return None
