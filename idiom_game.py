# encoding:utf-8
import json
import time
import requests
import plugins
import os
import threading
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *
import re
from config import conf

@plugins.register(
    name="IdiomGame",
    desire_priority=20,
    hidden=False,
    desc="成语猜猜乐游戏插件",
    version="1.0",
    author="Garson",
)
class IdiomGame(Plugin):
    """成语猜猜乐游戏插件
    
    功能：
    1. 开始新游戏
    2. 获取提示
    3. 提交答案
    4. 支持群聊和私聊
    5. 5题一轮游戏机制
    6. 支持跳过当前题目
    """
    # 默认配置
    DEFAULT_CONFIG = {
        "api_url": "https://xiaoapi.cn/API/game_ktccy.php",
        "cache_timeout": 300,  # 缓存超时时间（5分钟）
        "questions_per_round": 5,  # 每轮题目数量
        "leaderboard_size": 10,  # 排行榜显示人数
        "gewechat_base_url": "",  # GeWeChat API 基础地址
        "gewechat_token": "",  # GeWeChat API Token
        "gewechat_app_id": "",  # GeWeChat APP ID
        "enable_openai": True,  # 是否启用OpenAI解释功能
        "openai_api_key": "",  # OpenAI API密钥
        "openai_model": "gpt-3.5-turbo",  # OpenAI模型
        "openai_timeout": 10,  # OpenAI请求超时时间(秒)
        "openai_api_base": "https://api.openai.com/v1",  # OpenAI API基础地址
        "auth_password": "",  # 管理员认证密码
    }

    def __init__(self):
        """初始化插件配置"""
        try:
            super().__init__()
            
            # 确保使用默认配置初始化
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            
            # 使用默认配置初始化
            for key, default_value in self.DEFAULT_CONFIG.items():
                if key not in self.config:
                    self.config[key] = default_value
            
            # 设置配置参数
            self.api_url = self.config.get("api_url")
            self.cache_timeout = self.config.get("cache_timeout", 300)
            self.questions_per_round = self.config.get("questions_per_round", 5)
            self.leaderboard_size = self.config.get("leaderboard_size", 10)
            self.gewechat_base_url = self.config.get("gewechat_base_url", "")
            self.gewechat_token = self.config.get("gewechat_token", "")
            self.gewechat_app_id = self.config.get("gewechat_app_id", "")
            self.auth_password = self.config.get("auth_password", "1122")
            
            # 管理员列表
            self.admin_users = set()
            
            # OpenAI配置
            self.enable_openai = self.config.get("enable_openai", True)
            self.openai_api_key = self.config.get("openai_api_key", "")
            self.openai_model = self.config.get("openai_model", "gpt-3.5-turbo")
            self.openai_timeout = self.config.get("openai_timeout", 10)
            self.openai_api_base = self.config.get("openai_api_base", "https://api.openai.com/v1")
            
            # 游戏设置
            game_settings = self.config.get("game_settings", {})
            self.time_limit = game_settings.get("time_limit", 20)  # 每题限时（秒）
            self.auto_next_delay = game_settings.get("auto_next_delay", 1)  # 自动进入下一题的延迟（秒）
            self.correct_answer_delay = game_settings.get("correct_answer_delay", 3)  # 答对后进入下一题的延迟（秒）
            
            # 游戏状态缓存
            self.game_states = {}  # 格式: {user_id: {"start_time": timestamp, "current_pic": url, "current_round": 1, "score": 0, "consecutive_correct": 0, "question_start_time": timestamp}}
            
            # 群成员信息缓存
            self.group_members_cache = {}  # 格式: {group_id: {"update_time": timestamp, "members": {wxid: nickname}}}
            self.group_members_lock = threading.Lock()
            
            # 排行榜数据
            self.leaderboard_file = os.path.join(os.path.dirname(__file__), "leaderboard.json")
            self.leaderboard = self._load_leaderboard()  # 加载现有排行榜数据
            
            # 检查排行榜文件是否存在，如果不存在则创建
            if not os.path.exists(self.leaderboard_file):
                self._save_leaderboard()  # 保存空排行榜以创建文件
                logger.info("[IdiomGame] 排行榜文件不存在，已创建新文件")
                
            # 当前轮次排行榜（只记录本轮游戏）
            self.current_round_scores = {}  # 格式: {user_id: {"name": nickname, "score": score}}

            # 初始化OpenAI助手
            if self.enable_openai:
                self.openai_helper = OpenAIHelper(
                    api_key=self.openai_api_key,
                    model=self.openai_model,
                    timeout=self.openai_timeout,
                    api_base=self.openai_api_base
                )
                logger.info("[IdiomGame] OpenAI助手初始化完成")
            
            # 初始化定时器线程
            self.timer_thread = None
            self.timer_running = False
            self.timer_lock = threading.Lock()
            
            logger.info(f"[IdiomGame] 初始化完成, config={self.config}")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            logger.error(f"[IdiomGame] 初始化异常：{str(e)}", exc_info=True)
            raise Exception("[IdiomGame] 初始化失败")

    def on_handle_context(self, e_context: EventContext):
        """处理消息上下文"""
        if e_context['context'].type != ContextType.TEXT:
            return

        content = e_context['context'].content
        context_kwargs = e_context['context'].kwargs
        msg = context_kwargs.get('msg')
        
        # 获取用户ID
        user_id = None
        if msg and hasattr(msg, 'Data') and isinstance(msg.Data, dict):
            from_user = msg.Data.get('FromUserName', {})
            if isinstance(from_user, dict) and 'string' in from_user:
                user_id = from_user['string']
        
        if not user_id and msg:
            if hasattr(msg, 'fromUser'):
                user_id = msg.fromUser
            elif hasattr(msg, 'FromUserName'):
                if isinstance(msg.FromUserName, dict) and 'string' in msg.FromUserName:
                    user_id = msg.FromUserName['string']
                else:
                    user_id = msg.FromUserName
        
        if not user_id:
            session_id = context_kwargs.get('session_id', '')
            if session_id:
                if '@@' in session_id:
                    user_id = session_id.split('@@')[0]
                elif '@' in session_id:
                    user_id = session_id.split('@')[0]
        
        # 清理过期缓存
        self._clean_expired_cache()
        
        # 处理管理员认证
        if content.startswith("猜成语认证"):
            password = content[5:].strip()  # 提取密码部分
            self._handle_auth(user_id, password, e_context)
            e_context.action = EventAction.BREAK_PASS
            return
        
        # 处理游戏命令
        if content in ["开始游戏", "猜成语"]:
            self._start_game(user_id, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "结束游戏":
            self._end_game(user_id, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "提示":
            self._get_hint(user_id, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content.startswith("问"):
            # 处理追问
            question = content[1:].strip()
            self._get_followup_hint(user_id, question, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content.startswith("我猜"):
            answer = content[2:].strip()
            self._submit_answer(user_id, answer, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "下一题":
            self._next_question(user_id, e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "猜成语帮助":
            self._show_help(e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "历史排行榜":
            self._show_leaderboard(e_context)
            e_context.action = EventAction.BREAK_PASS
        elif content == "重置历史排行榜":
            # 确保有有效的用户ID
            if not user_id:
                session_id = context_kwargs.get('session_id', '')
                if session_id:
                    if '@@' in session_id:
                        user_id = session_id.split('@@')[0]
                    elif '@' in session_id:
                        user_id = session_id.split('@')[0]
                    else:
                        user_id = session_id
                logger.debug(f"[IdiomGame] 使用session_id作为用户ID: {user_id}")
            
            if not user_id:
                self._send_text_reply("无法获取用户ID，请重试", e_context)
                e_context.action = EventAction.BREAK_PASS
                return
                
            if not self.admin_users:  # 如果没有管理员，提示需要认证
                self._send_text_reply("请先进行管理员认证。发送'猜成语认证+密码'进行认证。", e_context)
            elif self._is_admin(user_id):
                self._reset_leaderboard(e_context)
            else:
                self._send_text_reply("抱歉，只有管理员才能重置历史排行榜。发送'猜成语认证+密码'进行管理员认证。", e_context)
            e_context.action = EventAction.BREAK_PASS

    def _handle_auth(self, user_id: str, password: str, e_context: EventContext):
        """处理管理员认证"""
        try:
            # 从消息对象获取用户ID
            msg_obj = e_context['context'].kwargs.get('msg')
            if msg_obj:
                # 尝试从Data字段获取
                if hasattr(msg_obj, 'Data') and isinstance(msg_obj.Data, dict):
                    from_user = msg_obj.Data.get('FromUserName', {})
                    if isinstance(from_user, dict) and 'string' in from_user:
                        user_id = from_user['string']
                        logger.debug(f"[IdiomGame] 从Data.FromUserName获取用户ID: {user_id}")
                
                # 尝试从其他字段获取
                if not user_id:
                    if hasattr(msg_obj, 'fromUser'):
                        user_id = msg_obj.fromUser
                    elif hasattr(msg_obj, 'FromUserName'):
                        if isinstance(msg_obj.FromUserName, dict) and 'string' in msg_obj.FromUserName:
                            user_id = msg_obj.FromUserName['string']
                        else:
                            user_id = msg_obj.FromUserName
            
            # 如果从消息对象获取失败，尝试从session_id获取
            if not user_id:
                session_id = e_context['context'].kwargs.get('session_id', '')
                if session_id:
                    # 直接使用session_id作为user_id，因为它是有效的wxid
                    user_id = session_id
                    logger.debug(f"[IdiomGame] 使用session_id作为用户ID: {user_id}")
            
            if not user_id:
                self._send_text_reply("认证失败，无法获取用户ID", e_context)
                logger.error("[IdiomGame] 管理员认证失败：无法获取用户ID")
                return
                
            if password == self.auth_password:
                # 使用标准化的用户ID
                normalized_id = self._normalize_user_id(user_id)
                # 保存原始完整ID
                self.admin_users.add(user_id)
                logger.info(f"[IdiomGame] 用户 {user_id} (normalized: {normalized_id}) 成功通过管理员认证")
                logger.debug(f"[IdiomGame] 当前管理员列表: {self.admin_users}")
                self._send_text_reply("✅ 管理员认证成功！现在您可以使用管理员功能了。", e_context)
            else:
                self._send_text_reply("❌ 认证失败，密码错误！", e_context)
                logger.warning(f"[IdiomGame] 用户 {user_id} 管理员认证失败，密码错误")
        except Exception as e:
            logger.error(f"[IdiomGame] 管理员认证异常: {str(e)}", exc_info=True)
            self._send_text_reply("认证过程出现错误，请稍后重试", e_context)

    def _is_admin(self, user_id: str) -> bool:
        """检查用户是否为管理员"""
        try:
            if not user_id:
                logger.warning("[IdiomGame] 用户ID为空，权限检查失败")
                return False
            
            # 直接检查原始ID是否在管理员列表中
            if user_id in self.admin_users:
                logger.info(f"[IdiomGame] 用户 {user_id} 是管理员（直接匹配）")
                return True
            
            # 如果直接匹配失败，尝试标准化后比对
            normalized_id = self._normalize_user_id(user_id)
            logger.debug(f"[IdiomGame] 权限检查 - 原始ID: {user_id}, 标准化ID: {normalized_id}")
            
            # 检查标准化后的ID是否匹配任何管理员
            for admin_id in self.admin_users:
                admin_normalized = self._normalize_user_id(admin_id)
                logger.debug(f"[IdiomGame] 比对管理员 - 原始ID: {admin_id}, 标准化ID: {admin_normalized}")
                if admin_normalized == normalized_id:
                    logger.info(f"[IdiomGame] 用户 {user_id} 是管理员（标准化匹配）")
                    return True
            
            logger.debug(f"[IdiomGame] 用户 {user_id} 不是管理员")
            return False
            
        except Exception as e:
            logger.error(f"[IdiomGame] 管理员检查异常: {str(e)}", exc_info=True)
            return False

    def _start_timer(self, user_id: str, e_context: EventContext):
        """启动定时器"""
        # 先停止现有定时器
        with self.timer_lock:
            self.timer_running = False
            if self.timer_thread and self.timer_thread.is_alive() and self.timer_thread != threading.current_thread():
                try:
                    self.timer_thread.join(0.1)  # 等待旧线程结束，最多等待0.1秒
                except Exception as e:
                    logger.warning(f"[IdiomGame] 停止旧定时器时出现异常: {str(e)}")
            self.timer_running = True
        
        def timer_callback():
            try:
                while True:
                    # 检查游戏状态是否还存在
                    if user_id not in self.game_states:
                        with self.timer_lock:
                            self.timer_running = False
                        return
                    
                    # 获取游戏状态
                    game_state = self.game_states[user_id]

                    # 检查是否已经回答过
                    if game_state.get("answered", False):
                        logger.debug(f"[IdiomGame] 用户已回答此题，定时器退出")
                        with self.timer_lock:
                            self.timer_running = False
                        return
                    
                    # 检查是否超时
                    current_time = time.time()
                    if current_time - game_state["question_start_time"] >= self.time_limit:
                        # 获取当前答案
                        current_answer = game_state["current_answer"]
                        
                        # 标记此题已处理，防止重复发送
                        game_state["answered"] = True
                        
                        # 获取成语解释
                        explanation = ""
                        if self.enable_openai and self.openai_helper.is_available():
                            try:
                                explanation = self.openai_helper.get_idiom_explanation(current_answer)
                            except Exception as e:
                                logger.error(f"[IdiomGame] 生成成语解释失败: {str(e)}", exc_info=True)
                        
                        # 发送超时消息
                        timeout_message = f"⏰ 时间到！\n\n正确答案是：{current_answer}"
                        if explanation:
                            timeout_message += f"\n\n📚 成语解析：\n{explanation}"
                        
                        # 添加过渡信息
                        if game_state["current_round"] < self.questions_per_round:
                            timeout_message += f"\n\n⏳ {self.auto_next_delay}秒后进入下一题..."
                        else:
                            timeout_message += "\n\n⏳ 即将显示本轮游戏结果..."
                        
                        # 使用channel直接发送而不是通过e_context
                        channel = e_context["channel"]
                        reply = Reply()
                        reply.type = ReplyType.TEXT
                        reply.content = timeout_message
                        channel.send(reply, e_context["context"])
                        
                        # 等待配置的延迟时间
                        time.sleep(self.auto_next_delay)
                        
                        # 如果是最后一题，直接结束游戏
                        if game_state["current_round"] >= self.questions_per_round:
                            # 获取用户昵称和当前分数，更新排行榜
                            user_name = self._get_user_name(e_context)
                            current_score = game_state.get("score", 0)
                            
                            # 确保有有效的用户ID（使用session_id作为备用）
                            effective_user_id = user_id
                            if not effective_user_id or effective_user_id == "None":
                                session_id = e_context['context'].kwargs.get('session_id', '')
                                if '@' in session_id:
                                    effective_user_id = session_id.split('@@')[0] if '@@' in session_id else session_id
                                    logger.debug(f"[IdiomGame] 使用session_id中的用户ID: {effective_user_id}")
                            
                            # 更新当前轮次排行榜
                            self._update_current_round_score(effective_user_id, user_name, current_score)
                            
                            # 更新历史排行榜
                            self._update_leaderboard(effective_user_id, user_name, current_score)
                            self._save_leaderboard()  # 确保保存排行榜数据
                            logger.info(f"[IdiomGame] 用户 {user_name} 完成游戏，得分 {current_score}，已更新排行榜")
                            
                            # 获取前三名信息
                            top_three = self._get_top_three(e_context)
                            
                            # 发送游戏结束消息
                            end_message = "🏆 本轮游戏结束！\n\n发送'猜成语'或'开始游戏'开始新一轮游戏"
                            reply = Reply()
                            reply.type = ReplyType.TEXT
                            reply.content = end_message
                            channel.send(reply, e_context["context"])
                            
                            # 等待1秒后发送排行榜
                            time.sleep(1)
                            
                            # 发送排行榜
                            leaderboard_message = f"🏆 本轮排行榜 🏆\n{top_three}\n\n发送'排行榜'查看历史排行"
                            reply = Reply()
                            reply.type = ReplyType.TEXT
                            reply.content = leaderboard_message
                            channel.send(reply, e_context["context"])
                            
                            # 删除游戏状态
                            if user_id in self.game_states:
                                del self.game_states[user_id]
                        else:
                            # 进入下一题
                            self._next_question(user_id, e_context)
                        
                        # 退出定时器循环
                        with self.timer_lock:
                            self.timer_running = False
                        return
                    
                    # 每秒检查一次
                    time.sleep(1)
                
            except Exception as e:
                logger.error(f"[IdiomGame] 定时器异常：{str(e)}", exc_info=True)
                with self.timer_lock:
                    self.timer_running = False
        
        # 初始化游戏状态中的answered字段为False
        if user_id in self.game_states:
            self.game_states[user_id]["answered"] = False
        
        # 启动定时器线程
        self.timer_thread = threading.Thread(target=timer_callback)
        self.timer_thread.daemon = True
        self.timer_thread.start()
        logger.debug(f"[IdiomGame] 启动定时器，时限{self.time_limit}秒")

    def _start_game(self, user_id: str, e_context: EventContext):
        """开始新游戏"""
        try:
            # 加载现有排行榜数据而不是清空
            self.leaderboard = self._load_leaderboard()
            logger.info("[IdiomGame] 新游戏开始，加载现有排行榜数据")
            
            # 清空当前轮次排行榜
            self.current_round_scores = {}
            logger.info("[IdiomGame] 已清空当前轮次排行榜")
            
            # 先停止任何正在运行的定时器和清除当前游戏状态
            with self.timer_lock:
                self.timer_running = False
                if user_id in self.game_states:
                    del self.game_states[user_id]
            
            response = requests.get(
                self.api_url,
                params={"msg": "开始游戏", "id": user_id}
            )
            data = response.json()
            
            if data["code"] == 200:
                # 保存游戏状态，包括当前题目的答案
                self.game_states[user_id] = {
                    "start_time": time.time(),
                    "current_pic": data["data"]["pic"],
                    "current_answer": data["data"]["answer"],  # 保存当前题目的答案
                    "current_round": 1,
                    "score": 0,
                    "consecutive_correct": 0,
                    "question_start_time": time.time(),  # 记录题目开始时间
                    "answered": False  # 初始化为未回答状态
                }
                
                # 使用channel直接发送消息
                channel = e_context["channel"]
                
                # 发送游戏开始提示
                start_message = (
                    f"欢迎来到成语猜猜乐！\n"
                    f"第1轮第1题（共{self.questions_per_round}题）\n"
                    f"⏰ 每题限时{self.time_limit}秒\n"
                    f"发送'提示'获取提示，发送'下一题'跳过当前题目"
                )
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = start_message
                channel.send(reply, e_context["context"])
                
                # 等待1秒后发送图片
                time.sleep(1)
                
                # 发送图片
                reply = Reply()
                reply.type = ReplyType.IMAGE_URL
                reply.content = data["data"]["pic"]
                channel.send(reply, e_context["context"])
                
                # 启动定时器
                self._start_timer(user_id, e_context)
            else:
                self._send_text_reply("游戏启动失败，请稍后再试", e_context)
                
        except Exception as e:
            logger.error(f"[IdiomGame] 开始游戏异常：{str(e)}", exc_info=True)
            self._send_text_reply("游戏启动失败，请稍后再试", e_context)

    def _next_question(self, user_id: str, e_context: EventContext):
        """进入下一题"""
        if user_id not in self.game_states:
            self._send_text_reply("请先开始游戏", e_context)
            return
            
        try:
            # 获取当前游戏状态
            game_state = self.game_states[user_id]
            current_round = game_state["current_round"]
            current_answer = game_state.get("current_answer", "")
            
            # 获取用户昵称
            user_name = self._get_user_name(e_context)
            
            # 使用channel直接发送消息
            channel = e_context["channel"]
            
            # 如果是通过"下一题"命令跳过，且此题未被标记为已回答，显示当前题目的答案和解析
            # 添加一个条件，确保不会在定时器已经触发后显示重复消息
            if current_answer and not game_state.get("answered", False):
                # 先标记为已回答，防止定时器重复处理
                game_state["answered"] = True
                
                # 尝试获取成语解释
                idiom_explanation = ""
                if self.enable_openai and self.openai_helper.is_available():
                    try:
                        explanation = self.openai_helper.get_idiom_explanation(current_answer)
                        if explanation:
                            idiom_explanation = f"\n\n📚 成语解析：\n{explanation}"
                            logger.info(f"[IdiomGame] 为成语'{current_answer}'生成AI解析")
                    except Exception as e:
                        logger.error(f"[IdiomGame] 生成成语解释失败: {str(e)}", exc_info=True)
                
                # 发送答案和解析
                skip_message = f"⏭️ 跳过本题\n当前题目答案：{current_answer}"
                if idiom_explanation:
                    skip_message += idiom_explanation
                
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = skip_message
                channel.send(reply, e_context["context"])
                
                # 等待2秒后继续
                time.sleep(2)
            
            # 检查是否完成一轮，或者即将超过配置的题目数量
            if current_round >= self.questions_per_round:
                # 获取用户昵称和当前分数，更新排行榜
                current_score = game_state.get("score", 0)
                
                # 确保有有效的用户ID（使用session_id作为备用）
                effective_user_id = user_id
                if not effective_user_id or effective_user_id == "None":
                    session_id = e_context['context'].kwargs.get('session_id', '')
                    if '@' in session_id:
                        effective_user_id = session_id.split('@@')[0] if '@@' in session_id else session_id
                        logger.debug(f"[IdiomGame] 使用session_id中的用户ID: {effective_user_id}")
                
                # 更新当前轮次排行榜
                self._update_current_round_score(effective_user_id, user_name, current_score)
                
                # 更新历史排行榜
                self._update_leaderboard(effective_user_id, user_name, current_score)
                self._save_leaderboard()  # 确保保存排行榜数据
                logger.info(f"[IdiomGame] 用户 {user_name} 完成游戏，得分 {current_score}，已更新排行榜")
                
                # 获取前三名信息
                top_three = self._get_top_three(e_context)
                
                # 发送游戏结束消息
                end_message = (
                    f"🏆 本轮游戏结束！\n\n"
                    f"🏆 本轮排行榜 🏆{top_three}\n\n"
                    f"发送'猜成语'或'开始游戏'开始新一轮游戏\n"
                    f"发送'历史排行榜'查看历史排行"
                )
                
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = end_message
                channel.send(reply, e_context["context"])
                
                # 删除游戏状态
                if user_id in self.game_states:
                    del self.game_states[user_id]
                return
                
            # 获取新题目
            response = requests.get(
                self.api_url,
                params={"msg": "开始游戏", "id": user_id}
            )
            data = response.json()
            
            if data["code"] == 200:
                # 增加当前轮次计数
                game_state["current_round"] += 1
                current_round = game_state["current_round"]
                
                # 确保不超过配置的题目数量
                if current_round > self.questions_per_round:
                    logger.warning(f"[IdiomGame] 题目计数超出预期: {current_round}/{self.questions_per_round}，强制结束游戏")
                    # 重复上面的游戏结束逻辑
                    current_score = game_state.get("score", 0)
                    
                    # 更新当前轮次排行榜
                    self._update_current_round_score(effective_user_id, user_name, current_score)
                    
                    # 更新历史排行榜
                    self._update_leaderboard(effective_user_id, user_name, current_score)
                    self._save_leaderboard()  # 确保保存排行榜数据
                    logger.info(f"[IdiomGame] 用户 {user_name} 完成游戏，得分 {current_score}，已更新排行榜")
                    
                    top_three = self._get_top_three(e_context)
                    
                    end_message = (
                        f"🏆 本轮游戏结束！\n\n"
                        f"🏆 本轮排行榜 🏆{top_three}\n\n"
                        f"发送'猜成语'或'开始游戏'开始新一轮游戏\n"
                        f"发送'历史排行榜'查看历史排行"
                    )
                    
                    reply = Reply()
                    reply.type = ReplyType.TEXT
                    reply.content = end_message
                    channel.send(reply, e_context["context"])
                    
                    if user_id in self.game_states:
                        del self.game_states[user_id]
                    return
                
                # 更新游戏状态，包括新题目的答案
                game_state["current_pic"] = data["data"]["pic"]
                game_state["current_answer"] = data["data"]["answer"]  # 保存新题目的答案
                game_state["question_start_time"] = time.time()  # 更新题目开始时间
                game_state["answered"] = False  # 重置答题状态
                
                # 发送文本提示
                text_reply = Reply()
                text_reply.type = ReplyType.TEXT
                text_reply.content = (
                    f"第1轮第{game_state['current_round']}题（共{self.questions_per_round}题）\n"
                    f"⏰ 每题限时{self.time_limit}秒\n"
                    f"发送'提示'获取提示，发送'下一题'跳过当前题目"
                )
                channel.send(text_reply, e_context["context"])
                
                # 等待1秒后发送图片
                time.sleep(1)
                
                # 发送图片
                image_reply = Reply()
                image_reply.type = ReplyType.IMAGE_URL
                image_reply.content = data["data"]["pic"]
                channel.send(image_reply, e_context["context"])
                
                # 启动定时器（确保在发送完图片后启动）
                logger.debug(f"[IdiomGame] 第{game_state['current_round']}题开始，启动定时器")
                self._start_timer(user_id, e_context)
            else:
                self._send_text_reply("获取新题目失败，请稍后再试", e_context)
                
        except Exception as e:
            logger.error(f"[IdiomGame] 获取新题目异常：{str(e)}")
            self._send_text_reply("获取新题目失败，请稍后再试", e_context)

    def _get_hint(self, user_id: str, e_context: EventContext):
        """获取提示"""
        if user_id not in self.game_states:
            self._send_text_reply("请先开始游戏", e_context)
            return
            
        try:
            # 获取当前答案
            current_answer = self.game_states[user_id]["current_answer"]
            
            # 添加OpenAI成语提示
            if self.enable_openai and current_answer and self.openai_helper.is_available():
                try:
                    idiom_hint = self.openai_helper.get_idiom_hint(current_answer, hint_level=1)
                    if idiom_hint:
                        hint_text = f"🔮 智能提示：{idiom_hint}\n\n💡 发送'问+问题'可以继续追问，获取更多提示"
                        self._send_text_reply(hint_text, e_context)
                        return
                except Exception as e:
                    logger.error(f"[IdiomGame] 生成成语提示失败: {str(e)}", exc_info=True)
            
            # 如果OpenAI不可用或生成失败，使用默认提示
            self._send_text_reply("暂时无法获取提示，请稍后再试", e_context)
                
        except Exception as e:
            logger.error(f"[IdiomGame] 获取提示异常：{str(e)}")
            self._send_text_reply("获取提示失败，请稍后再试", e_context)

    def _get_followup_hint(self, user_id: str, question: str, e_context: EventContext):
        """获取追问提示"""
        if user_id not in self.game_states:
            self._send_text_reply("请先开始游戏", e_context)
            return
            
        try:
            # 获取当前答案
            current_answer = self.game_states[user_id]["current_answer"]
            
            # 添加OpenAI成语追问提示
            if self.enable_openai and current_answer and self.openai_helper.is_available():
                try:
                    # 根据问题内容调整提示级别
                    hint_level = 2  # 默认使用更详细的提示
                    if "意思" in question or "含义" in question:
                        hint_level = 3  # 提供更直接的提示
                    elif "出处" in question or "来源" in question:
                        hint_level = 4  # 提供典故相关的提示
                    
                    idiom_hint = self.openai_helper.get_idiom_hint(current_answer, hint_level=hint_level)
                    if idiom_hint:
                        hint_text = f"🔮 追问提示：{idiom_hint}\n\n💡 发送'问+问题'可以继续追问，获取更多提示"
                        self._send_text_reply(hint_text, e_context)
                        return
                except Exception as e:
                    logger.error(f"[IdiomGame] 生成追问提示失败: {str(e)}", exc_info=True)
            
            # 如果OpenAI不可用或生成失败，使用默认提示
            self._send_text_reply("暂时无法获取追问提示，请稍后再试", e_context)
                
        except Exception as e:
            logger.error(f"[IdiomGame] 获取追问提示异常：{str(e)}")
            self._send_text_reply("获取追问提示失败，请稍后再试", e_context)

    def _submit_answer(self, user_id: str, answer: str, e_context: EventContext):
        """提交答案"""
        if user_id not in self.game_states:
            self._send_text_reply("请先开始游戏", e_context)
            return
            
        try:
            game_state = self.game_states[user_id]
            current_answer = game_state["current_answer"]
            
            # 获取用户昵称
            user_name = self._get_user_name(e_context)
            
            # 检查答案是否正确
            if answer == current_answer:
                # 答案正确
                game_state["score"] += 1  # 每答对一题加1分
                game_state["consecutive_correct"] += 1
                current_score = game_state["score"]
                consecutive_correct = game_state["consecutive_correct"]
                
                # 标记此题已回答，防止定时器重复发送超时消息
                game_state["answered"] = True
                
                # 确保有有效的用户ID
                effective_user_id = user_id
                if not effective_user_id or effective_user_id == "None":
                    session_id = e_context['context'].kwargs.get('session_id', '')
                    if '@' in session_id:
                        effective_user_id = session_id.split('@@')[0] if '@@' in session_id else session_id
                        logger.debug(f"[IdiomGame] 使用session_id中的用户ID: {effective_user_id}")
                
                # 更新当前轮次排行榜
                self._update_current_round_score(effective_user_id, user_name, current_score)
                
                # 计算用时
                time_used = int(time.time() - game_state["question_start_time"])
                
                # 尝试获取成语解释
                idiom_explanation = ""
                if self.enable_openai and self.openai_helper.is_available():
                    try:
                        explanation = self.openai_helper.get_idiom_explanation(current_answer)
                        if explanation:
                            idiom_explanation = f"\n\n📚 成语解析：\n{explanation}"
                            logger.info(f"[IdiomGame] 为成语'{current_answer}'生成AI解析")
                    except Exception as e:
                        logger.error(f"[IdiomGame] 生成成语解释失败: {str(e)}", exc_info=True)
                
                # 检查是否完成一轮
                if game_state["current_round"] >= self.questions_per_round:
                    # 更新排行榜
                    self._update_leaderboard(effective_user_id, user_name, current_score)
                    self._save_leaderboard()  # 确保保存排行榜数据
                    logger.info(f"[IdiomGame] 用户 {user_name} 完成游戏，得分 {current_score}，已更新排行榜")
                    
                    # 获取前三名信息
                    top_three = self._get_top_three(e_context)
                    
                    # 使用channel直接发送消息
                    channel = e_context["channel"]
                    
                    # 1. 先发送答对消息和解析
                    answer_message = (
                        f"🎉 {user_name} 回答正确！+1分\n"
                        f"⏱ 用时：{time_used}秒"
                    )
                    if idiom_explanation:
                        answer_message += idiom_explanation
                    
                    reply = Reply()
                    reply.type = ReplyType.TEXT
                    reply.content = answer_message
                    channel.send(reply, e_context["context"])
                    
                    # 2. 等待2秒后发送游戏结束消息
                    time.sleep(2)
                    
                    # 3. 发送游戏结束和排行榜消息
                    end_message = (
                        f"🏆 本轮游戏结束！\n\n"
                        f"🏆 本轮排行榜 🏆{top_three}\n\n"
                        f"发送'猜成语'或'开始游戏'开始新一轮游戏\n"
                        f"发送'历史排行榜'查看历史排行"
                    )
                    
                    reply = Reply()
                    reply.type = ReplyType.TEXT
                    reply.content = end_message
                    channel.send(reply, e_context["context"])
                    
                    # 删除游戏状态
                    del self.game_states[user_id]
                else:
                    # 发送答对消息 - 简化版本
                    answer_content = (
                        f"🎉 {user_name} 回答正确！+1分\n"
                        f"⏱ 用时：{time_used}秒"
                    )
                    
                    if idiom_explanation:
                        answer_content += idiom_explanation + "\n"
                    
                    answer_content += f"\n🎮 {self.correct_answer_delay}秒后自动开始下一题..."
                    
                    reply = Reply()
                    reply.type = ReplyType.TEXT
                    reply.content = answer_content
                    
                    # 使用channel直接发送而不是通过e_context
                    channel = e_context["channel"]
                    channel.send(reply, e_context["context"])
                    
                    # 等待配置的延迟时间后自动进入下一题
                    time.sleep(self.correct_answer_delay)
                    self._next_question(user_id, e_context)
            else:
                # 答案错误，重置连续答对次数
                game_state["consecutive_correct"] = 0
                
                # 获取提示
                try:
                    response = requests.get(
                        self.api_url,
                        params={"msg": "提示", "id": user_id},
                        timeout=5
                    )
                    data = response.json()
                    if data["code"] == 200:
                        hint_text = data["data"]["msg"].replace("n", "\n").strip()
                        self._send_text_reply(hint_text, e_context)
                    else:
                        self._send_text_reply("答案错误，请再试一次", e_context)
                except Exception as e:
                    logger.error(f"[IdiomGame] 获取提示异常：{str(e)}")
                    self._send_text_reply("答案错误，请再试一次", e_context)
                
        except Exception as e:
            logger.error(f"[IdiomGame] 提交答案异常：{str(e)}")
            self._send_text_reply("提交答案失败，请稍后再试", e_context)

    def _show_help(self, e_context: EventContext):
        """显示帮助信息"""
        help_text = """成语猜猜乐游戏帮助：
1. 发送"猜成语"或"开始游戏"开始新游戏（每轮5题）
2. 发送"提示"获取智能提示
3. 发送"问+问题"获取更多提示（例如：问意思、问出处）
4. 发送"我猜xxx"提交答案（xxx为猜测的成语）
5. 发送"下一题"跳过当前题目
6. 发送"结束游戏"结束当前游戏
7. 发送"历史排行榜"查看成绩排行
8. 发送"帮助"查看本帮助信息

管理员功能：
1. 发送"猜成语认证+密码"进行管理员认证
2. 发送"重置历史排行榜"清空所有排行榜数据（仅限管理员）"""
            
        self._send_text_reply(help_text, e_context)

    def _send_text_reply(self, content: str, e_context: EventContext):
        """发送文本回复"""
        reply = Reply()
        reply.type = ReplyType.TEXT
        reply.content = content
        e_context['reply'] = reply
        e_context.action = EventAction.BREAK_PASS

    def _clean_expired_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        expired_users = [
            user_id for user_id, state in self.game_states.items()
            if current_time - state["start_time"] > self.cache_timeout
        ]
        for user_id in expired_users:
            del self.game_states[user_id]

    def get_help_text(self, verbose=False, **kwargs):
        """获取插件帮助文本"""
        help_text = "成语猜猜乐游戏插件\n"
        if verbose:
            help_text += """使用说明：
1. 发送"猜成语"或"开始游戏"开始新游戏（每轮5题）
2. 发送"提示"获取提示
3. 发送"我猜xxx"提交答案
4. 发送"下一题"跳过当前题目
5. 发送"结束游戏"结束当前游戏
6. 发送"排行榜"查看成绩排行
7. 发送"帮助"查看帮助信息"""
        return help_text

    def _load_config_template(self):
        """加载配置模板"""
        return self.DEFAULT_CONFIG 

    def _load_leaderboard(self):
        """加载排行榜数据"""
        try:
            if os.path.exists(self.leaderboard_file):
                with open(self.leaderboard_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}  # 如果文件不存在，返回空字典
        except Exception as e:
            logger.error(f"[IdiomGame] 加载排行榜异常：{str(e)}")
            return {}
    
    def _save_leaderboard(self):
        """保存排行榜数据"""
        try:
            with open(self.leaderboard_file, 'w', encoding='utf-8') as f:
                json.dump(self.leaderboard, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[IdiomGame] 保存排行榜异常：{str(e)}")
    
    def _update_leaderboard(self, user_id, user_name, score):
        """更新排行榜"""
        try:
            # 如果用户ID为None，尝试使用用户名作为ID
            if not user_id:
                logger.warning(f"[IdiomGame] 更新排行榜时用户ID为空，尝试使用用户名: {user_name}")
                if user_name and user_name not in ["未知用户", "玩家"]:
                    user_id = f"name_{user_name}"  # 添加前缀，避免冲突
                else:
                    logger.error("[IdiomGame] 无法更新排行榜：用户ID和用户名都无效")
                    return
            
            # 标准化用户ID，去除可能的前缀和后缀
            normalized_id = self._normalize_user_id(user_id)
            
            # 检查是否已有该用户的其他ID形式存在于排行榜中
            existing_entry = None
            for lid, data in self.leaderboard.items():
                if lid and self._normalize_user_id(lid) == normalized_id:
                    existing_entry = lid
                    break
            
            if existing_entry:
                # 使用现有条目更新
                user_data = self.leaderboard[existing_entry]
                user_data["best_score"] = max(user_data["best_score"], score)
                user_data["games_played"] += 1
                user_data["total_score"] += score
                if user_name and user_name not in ["未知用户", "玩家"]:
                    user_data["name"] = user_name  # 只在有有效名称时更新
                logger.debug(f"[IdiomGame] 更新现有排行榜记录: {user_name}, 最高分: {user_data['best_score']}")
            else:
                # 创建新条目
                self.leaderboard[user_id] = {
                    "name": user_name if user_name and user_name not in ["未知用户", "玩家"] else f"用户{normalized_id[-6:]}",
                    "best_score": score,
                    "games_played": 1,
                    "total_score": score
                }
                logger.debug(f"[IdiomGame] 新增排行榜用户: {user_id}, 昵称: {user_name}")
            
            # 保存排行榜
            self._save_leaderboard()
            
        except Exception as e:
            logger.error(f"[IdiomGame] 更新排行榜异常：{str(e)}", exc_info=True)

    def _normalize_user_id(self, user_id):
        """标准化用户ID，去除前缀和后缀，方便比较"""
        if not isinstance(user_id, str):
            return user_id
            
        # 处理群聊消息中的用户ID（格式可能是 wxid_xxx@@groupid）
        if '@@' in user_id:
            user_id = user_id.split('@@')[0]
            
        # 处理wxid格式
        if user_id.startswith('wxid_'):
            return user_id.split('_')[1][:8]  # 提取ID的特征部分
            
        # 处理其他格式（如带@chatroom后缀）
        if '@' in user_id:
            return user_id.split('@')[0]
            
        return user_id

    def _show_leaderboard(self, e_context: EventContext):
        """显示历史排行榜"""
        if not self.leaderboard:
            self._send_text_reply("历史排行榜暂无数据", e_context)
            return
        
        # 检查是否是群聊消息
        context_kwargs = e_context['context'].kwargs
        is_group = context_kwargs.get('isgroup', False)
        group_id = None
        group_members = None
        
        if is_group:
            # 获取群ID
            msg_obj = context_kwargs.get('msg')
            if msg_obj and hasattr(msg_obj, 'receiver'):
                group_id = msg_obj.receiver
            if not group_id:
                group_id = context_kwargs.get('receiver')
            
            if group_id and '@chatroom' in group_id:
                # 获取群成员列表
                with self.group_members_lock:
                    if group_id in self.group_members_cache:
                        group_members = set(self.group_members_cache[group_id].get("members", {}).keys())
                        logger.debug(f"[IdiomGame] 获取到群 {group_id} 的成员列表: {len(group_members)} 人")
        
        # 合并同一用户的不同记录
        merged_leaderboard = {}
        for user_id, data in self.leaderboard.items():
            # 如果是群聊且有群成员列表，只显示群成员的记录
            if is_group and group_members is not None:
                normalized_id = self._normalize_user_id(user_id)
                # 检查用户是否在群成员列表中
                user_in_group = False
                for member_id in group_members:
                    if self._normalize_user_id(member_id) == normalized_id:
                        user_in_group = True
                        break
                if not user_in_group:
                    continue
            
            normalized_id = self._normalize_user_id(user_id)
            if normalized_id in merged_leaderboard:
                # 合并数据
                merged_data = merged_leaderboard[normalized_id]
                merged_data["best_score"] = max(merged_data["best_score"], data["best_score"])
                merged_data["total_score"] += data["total_score"]
                merged_data["games_played"] += data["games_played"]
                # 优先使用更好的名称
                if data["name"] and data["name"] not in ["未知用户", "玩家"] and not data["name"].startswith("用户"):
                    merged_data["name"] = data["name"]
            else:
                merged_leaderboard[normalized_id] = data.copy()
        
        # 按最高分降序排序
        sorted_users = sorted(
            merged_leaderboard.items(),
            key=lambda x: (x[1]["best_score"], x[1]["total_score"]),
            reverse=True
        )
        
        # 生成排行榜文本
        title = "📊 成语猜猜乐历史排行榜 📊"
        if is_group and group_members is not None:
            title = "📊 本群成语猜猜乐历史排行榜 📊"
        leaderboard_text = f"{title}\n\n"
        
        for i, (uid, data) in enumerate(sorted_users[:self.leaderboard_size]):
            medal = ""
            if i == 0:
                medal = "🥇"
            elif i == 1:
                medal = "🥈"
            elif i == 2:
                medal = "🥉"
            
            name = data['name']
            # 确保名称显示正确
            if not name or name in ['未知用户', '玩家'] or name.startswith("用户"):
                if uid.startswith('wxid_'):
                    name = f"用户{uid.split('_')[-1][:6]}"
                else:
                    name = f"用户{uid[:6]}"
            
            leaderboard_text += f"{i+1}. {medal} {name}\n"
            leaderboard_text += f"   最高分: {data['best_score']} | 总分: {data['total_score']} | 游戏次数: {data['games_played']}\n"
        
        # 如果当前正在进行游戏，也显示当前轮次的排行
        if self.current_round_scores:
            leaderboard_text += "\n📈 当前轮次排行 📈\n"
            
            # 按分数降序排序
            sorted_current = sorted(
                self.current_round_scores.items(),
                key=lambda x: x[1]["score"],
                reverse=True
            )
            
            for i, (uid, data) in enumerate(sorted_current[:3]):
                medal = ""
                if i == 0:
                    medal = "🥇"
                elif i == 1:
                    medal = "🥈"
                elif i == 2:
                    medal = "🥉"
                
                name = data['name']
                score = data['score']
                
                leaderboard_text += f"{i+1}. {medal} {name}: {score}分\n"
        
        self._send_text_reply(leaderboard_text, e_context)

    def _get_user_rank(self, user_id):
        """获取用户排名"""
        if not user_id:
            logger.warning("[IdiomGame] 获取排名时用户ID为空")
            return None
            
        # 检查用户是否在排行榜中（直接或通过标准化ID）
        normalized_id = self._normalize_user_id(user_id)
        target_id = None
        
        # 先直接检查原始ID
        if user_id in self.leaderboard:
            target_id = user_id
        else:
            # 然后检查标准化ID
            for lid in self.leaderboard.keys():
                if lid and self._normalize_user_id(lid) == normalized_id:
                    target_id = lid
                    break
        
        if not target_id:
            logger.debug(f"[IdiomGame] 用户 {user_id} 不在排行榜中")
            return None
            
        # 创建排行榜副本进行排序
        ranking_list = []
        processed_ids = set()
        
        for lid, data in self.leaderboard.items():
            if not lid:  # 跳过无效ID
                continue
                
            norm_id = self._normalize_user_id(lid)
            if norm_id in processed_ids:
                # 跳过已处理的标准化ID，避免重复
                continue
                
            processed_ids.add(norm_id)
            ranking_list.append((lid, data["best_score"], data["total_score"]))
        
        # 按最高分和总分降序排序
        sorted_ranking = sorted(
            ranking_list, 
            key=lambda x: (x[1], x[2]), 
            reverse=True
        )
        
        # 查找目标用户的排名
        target_norm_id = self._normalize_user_id(target_id)
        for i, (lid, _, _) in enumerate(sorted_ranking):
            if self._normalize_user_id(lid) == target_norm_id:
                rank = i + 1
                logger.debug(f"[IdiomGame] 用户 {user_id} 排名: 第{rank}名，共{len(sorted_ranking)}人")
                return rank
        
        logger.debug(f"[IdiomGame] 用户 {user_id} 排名未找到")
        return None
    
    def _get_user_name(self, e_context: EventContext):
        """获取用户昵称"""
        try:
            # 从消息上下文中获取用户信息
            context_kwargs = e_context['context'].kwargs
            
            # 获取消息对象
            msg_obj = context_kwargs.get('msg')
            is_group = context_kwargs.get('isgroup', False)
            
            # 更详细的日志，帮助调试
            if hasattr(msg_obj, '__dict__'):
                msg_dict = msg_obj.__dict__
                logger.debug(f"[IdiomGame] 消息对象完整属性: {msg_dict}")
            else:
                logger.debug(f"[IdiomGame] 消息对象: {type(msg_obj)}")
            
            logger.debug(f"[IdiomGame] 上下文参数: {context_kwargs}")
            
            # 优先使用actual_user_nickname
            if hasattr(msg_obj, 'actual_user_nickname') and msg_obj.actual_user_nickname:
                nickname = msg_obj.actual_user_nickname
                logger.debug(f"[IdiomGame] 从actual_user_nickname获取昵称: {nickname}")
                return nickname
            
            # 其次使用other_user_nickname
            if hasattr(msg_obj, 'other_user_nickname') and msg_obj.other_user_nickname:
                nickname = msg_obj.other_user_nickname
                logger.debug(f"[IdiomGame] 从other_user_nickname获取昵称: {nickname}")
                return nickname
            
            # 获取用户ID - 优先从微信消息对象获取发送者ID
            user_id = None
            
            # GeWeChat特有的字段
            if hasattr(msg_obj, 'fromUser'):
                user_id = msg_obj.fromUser
                logger.debug(f"[IdiomGame] 从fromUser获取用户ID: {user_id}")
            
            # 尝试从push_content获取昵称（小写形式）
            if hasattr(msg_obj, 'push_content') and msg_obj.push_content:
                push_content = msg_obj.push_content
                if ' : ' in push_content:
                    nickname = push_content.split(' : ')[0].strip()
                    if nickname:
                        logger.debug(f"[IdiomGame] 从push_content获取昵称: {nickname}")
                        return nickname
            
            # 尝试从PushContent获取昵称（大写形式）
            if hasattr(msg_obj, 'PushContent') and msg_obj.PushContent:
                push_content = msg_obj.PushContent
                if ' : ' in push_content:
                    nickname = push_content.split(' : ')[0].strip()
                    if nickname:
                        logger.debug(f"[IdiomGame] 从PushContent获取昵称: {nickname}")
                        return nickname
            
            # 其他可能的字段
            if not user_id and hasattr(msg_obj, 'from_user_id'):
                user_id = msg_obj.from_user_id
                logger.debug(f"[IdiomGame] 从from_user_id获取用户ID: {user_id}")
            
            # 从session_id中提取用户ID
            if not user_id and 'session_id' in context_kwargs:
                session_id = context_kwargs.get('session_id', '')
                logger.debug(f"[IdiomGame] session_id: {session_id}")
                
                # 群聊消息的session_id格式通常是 userID@@groupID
                if '@@' in session_id:
                    user_id = session_id.split('@@')[0]
                    logger.debug(f"[IdiomGame] 从session_id分割获取用户ID: {user_id}")
            
            # 检查获取的用户ID是否是群ID
            if user_id and '@chatroom' in user_id:
                logger.warning(f"[IdiomGame] 获取到的用户ID {user_id} 看起来像是群ID，尝试从其他来源获取")
                user_id = None  # 重置用户ID
                
                # 尝试从session_id中提取用户部分
                if 'session_id' in context_kwargs:
                    session_id = context_kwargs.get('session_id', '')
                    if '@@' in session_id:
                        user_id = session_id.split('@@')[0]
                        logger.debug(f"[IdiomGame] 重新从session_id获取用户ID: {user_id}")
            
            logger.debug(f"[IdiomGame] 最终用户ID: {user_id}, 是否群聊: {is_group}")
            
            # 如果是群聊且有用户ID，尝试获取群成员昵称
            if is_group and user_id:
                # 获取群ID
                group_id = context_kwargs.get('receiver')
                if group_id and '@chatroom' in group_id:
                    nickname = self._get_group_member_nickname(group_id, user_id)
                    if nickname:
                        logger.debug(f"[IdiomGame] 获取到群成员昵称: {nickname}")
                        return nickname
            
            # 如果无法从群信息获取，尝试从其他来源获取昵称
            if hasattr(msg_obj, 'sender') and isinstance(msg_obj.sender, dict) and 'nickname' in msg_obj.sender:
                nickname = msg_obj.sender['nickname']
                logger.debug(f"[IdiomGame] 从sender.nickname获取昵称: {nickname}")
                return nickname
            
            if hasattr(msg_obj, 'from_user_nickname') and msg_obj.from_user_nickname:
                nickname = msg_obj.from_user_nickname
                logger.debug(f"[IdiomGame] 从from_user_nickname获取昵称: {nickname}")
                return nickname
            
            # 如果都获取不到，格式化用户ID作为昵称
            if user_id:
                if user_id.startswith('wxid_'):
                    nickname = f"用户{user_id.split('_')[-1][:6]}"  # 恢复使用格式化的昵称
                    logger.debug(f"[IdiomGame] 格式化wxid为昵称: {nickname}")
                    return nickname
                logger.debug(f"[IdiomGame] 使用用户ID作为昵称: {user_id}")
                return user_id
            
            logger.debug("[IdiomGame] 无法获取用户信息，使用默认昵称'玩家'")
            return "玩家"
        except Exception as e:
            logger.error(f"[IdiomGame] 获取用户昵称异常：{str(e)}", exc_info=True)
            return "玩家"
    
    def _get_group_member_nickname(self, group_id, user_id):
        """从群成员缓存获取用户昵称"""
        with self.group_members_lock:
            # 检查缓存是否存在且未过期
            current_time = time.time()
            if (group_id in self.group_members_cache and 
                current_time - self.group_members_cache[group_id].get("update_time", 0) < 3600):  # 1小时缓存
                members = self.group_members_cache[group_id].get("members", {})
                if user_id in members:
                    return members[user_id]
            
            # 缓存不存在或已过期，尝试获取群成员信息
            try:
                # 请求群信息
                url = f"{self.gewechat_base_url}/group/getChatroomInfo"
                headers = {
                    "X-GEWE-TOKEN": self.gewechat_token,
                    "Content-Type": "application/json"
                }
                data = {
                    "appId": self.gewechat_app_id,
                    "chatroomId": group_id
                }
                
                logger.debug(f"[IdiomGame] 请求群信息 - URL: {url}, Data: {data}")
                
                response = requests.post(url, headers=headers, json=data, timeout=5)
                result = response.json()
                
                if result.get("ret") == 200 and "data" in result:
                    # 处理获取到的群成员信息
                    members = {}
                    for member in result["data"].get("memberList", []):
                        wxid = member.get("wxid")
                        nickname = member.get("nickName") or member.get("displayName")
                        if wxid and nickname:
                            members[wxid] = nickname
                    
                    # 更新缓存
                    self.group_members_cache[group_id] = {
                        "update_time": current_time,
                        "members": members
                    }
                    
                    logger.info(f"[IdiomGame] 成功获取群 {group_id} 的成员信息，共 {len(members)} 人")
                    
                    # 返回请求的用户昵称
                    if user_id in members:
                        return members[user_id]
                else:
                    logger.warning(f"[IdiomGame] 获取群成员信息失败: {result}")
            except Exception as e:
                logger.error(f"[IdiomGame] 获取群成员信息异常: {str(e)}", exc_info=True)
        
        return None 

    def _reset_leaderboard(self, e_context: EventContext):
        """重置排行榜"""
        try:
            self.leaderboard = {}
            self._save_leaderboard()
            self._send_text_reply("排行榜已重置", e_context)
            logger.info("[IdiomGame] 排行榜已被管理员重置")
        except Exception as e:
            logger.error(f"[IdiomGame] 重置排行榜异常: {str(e)}")
            self._send_text_reply("重置排行榜失败", e_context)
            
    def _clean_leaderboard(self, e_context: EventContext):
        """清理排行榜中的重复和无效数据"""
        try:
            old_count = len(self.leaderboard)
            
            # 临时存储合并后的数据
            merged = {}
            removed = []
            
            # 第一步：移除None和空键
            invalid_keys = [k for k in list(self.leaderboard.keys()) if not k]
            for k in invalid_keys:
                removed.append(f"空ID: {k}")
                del self.leaderboard[k]
            
            # 第二步：合并相同用户的不同记录
            for user_id, data in list(self.leaderboard.items()):
                normalized_id = self._normalize_user_id(user_id)
                
                if normalized_id in merged:
                    # 合并数据
                    merged_data = merged[normalized_id]
                    merged_data["best_score"] = max(merged_data["best_score"], data["best_score"])
                    merged_data["total_score"] += data["total_score"]
                    merged_data["games_played"] += data["games_played"]
                    
                    # 使用更好的名称
                    if (data["name"] and data["name"] not in ["未知用户", "玩家"] 
                        and not data["name"].startswith("用户")):
                        merged_data["user_id"] = user_id
                        merged_data["name"] = data["name"]
                    
                    # 记录被合并的项
                    removed.append(f"{user_id} -> {merged_data['user_id']}")
                    
                    # 从原始数据中删除
                    del self.leaderboard[user_id]
                else:
                    # 新记录
                    merged[normalized_id] = data.copy()
                    merged[normalized_id]["user_id"] = user_id
            
            # 第三步：用合并后的数据更新排行榜
            new_leaderboard = {}
            for _, data in merged.items():
                user_id = data.pop("user_id", None)
                if user_id:
                    new_leaderboard[user_id] = data
            
            # 更新并保存
            self.leaderboard = new_leaderboard
            self._save_leaderboard()
            
            # 发送结果
            new_count = len(self.leaderboard)
            removed_count = old_count - new_count
            
            result = f"排行榜已清理完成\n原始数据: {old_count}条\n清理后: {new_count}条\n移除/合并: {removed_count}条"
            if removed:
                result += "\n\n已合并的条目:\n" + "\n".join(removed[:5])
                if len(removed) > 5:
                    result += f"\n...等共{len(removed)}条"
                    
            self._send_text_reply(result, e_context)
            logger.info(f"[IdiomGame] 排行榜已清理: 从{old_count}条减少到{new_count}条")
            
        except Exception as e:
            logger.error(f"[IdiomGame] 清理排行榜异常: {str(e)}", exc_info=True)
            self._send_text_reply("清理排行榜失败", e_context) 

    def _update_current_round_score(self, user_id, user_name, score):
        """更新当前轮次排行榜"""
        try:
            # 更完备的用户ID校验
            if not user_id or user_id == "None":
                logger.warning(f"[IdiomGame] 更新当前轮次排行榜时用户ID为空，使用用户名: {user_name}")
                if user_name and user_name not in ["未知用户", "玩家"]:
                    user_id = f"name_{user_name}"  # 添加前缀，避免冲突
                else:
                    logger.warning("[IdiomGame] 无法更新当前轮次排行榜：用户ID和用户名都无效")
                    return
            
            # 更新当前轮次分数
            self.current_round_scores[user_id] = {
                "name": user_name if user_name and user_name != "未知用户" and user_name != "玩家" else f"用户{user_id[-6:] if isinstance(user_id, str) and len(user_id) > 6 else user_id}",
                "score": score
            }
            
            logger.debug(f"[IdiomGame] 已更新当前轮次排行榜：用户 {user_name}，得分 {score}")
        except Exception as e:
            logger.error(f"[IdiomGame] 更新当前轮次排行榜异常：{str(e)}", exc_info=True)

    def _get_top_three(self, e_context: EventContext):
        """获取排行榜前三名（仅当前轮次）"""
        if not self.current_round_scores:
            return "暂无排行数据"
            
        # 按分数降序排序
        sorted_users = sorted(
            self.current_round_scores.items(),
            key=lambda x: x[1]["score"],
            reverse=True
        )
        
        # 生成前三名文本
        top_three_text = ""
        
        # 只获取前三名
        top_three = sorted_users[:3]
        
        if not top_three:
            return "暂无排行数据"
            
        for i, (uid, data) in enumerate(top_three):
            medal = ""
            if i == 0:
                medal = "🥇"
            elif i == 1:
                medal = "🥈"
            elif i == 2:
                medal = "🥉"
            
            name = data['name']
            score = data['score']
            
            top_three_text += f"\n{i+1}. {medal} {name}: {score}分"
        
        return top_three_text

    def _end_game(self, user_id: str, e_context: EventContext):
        """结束当前游戏"""
        try:
            # 检查是否有游戏在进行
            if user_id not in self.game_states:
                self._send_text_reply("当前没有进行中的游戏", e_context)
                return
                
            # 获取游戏状态和用户信息
            game_state = self.game_states[user_id]
            user_name = self._get_user_name(e_context)
            current_score = game_state.get("score", 0)
            current_round = game_state.get("current_round", 0)
            
            # 确保有有效的用户ID（使用session_id作为备用）
            effective_user_id = user_id
            if not effective_user_id or effective_user_id == "None":
                session_id = e_context['context'].kwargs.get('session_id', '')
                if '@' in session_id:
                    effective_user_id = session_id.split('@@')[0] if '@@' in session_id else session_id
                    logger.debug(f"[IdiomGame] 使用session_id中的用户ID: {effective_user_id}")
            
            # 停止定时器
            with self.timer_lock:
                self.timer_running = False
            
            # 记录得分（如果已答题）
            if current_round > 0:
                # 更新当前轮次排行榜
                self._update_current_round_score(effective_user_id, user_name, current_score)
                
                # 更新历史排行榜（只有当得分大于0时）
                if current_score > 0:
                    self._update_leaderboard(effective_user_id, user_name, current_score)
                    self._save_leaderboard()
                    logger.info(f"[IdiomGame] 用户 {user_name} 提前结束游戏，得分 {current_score}，已更新排行榜")
            
            # 获取答案
            current_answer = game_state.get("current_answer", "")
            
            # 构造结束游戏消息
            end_message = f"🛑 游戏已结束\n\n"
            
            # 显示当前题目的答案（如果有）
            if current_answer:
                end_message += f"当前题目答案: {current_answer}\n\n"
            
            # 显示得分情况
            end_message += f"共完成 {current_round} 题，得分: {current_score}"
            
            # 如果有得分，显示排行榜
            if current_score > 0:
                top_three = self._get_top_three(e_context)
                end_message += f"\n\n🏆 本轮排行榜 🏆\n{top_three}"
            
            end_message += "\n\n发送'猜成语'或'开始游戏'开始新一轮游戏\n发送'历史排行榜'查看历史排行"
            
            # 发送消息
            self._send_text_reply(end_message, e_context)
            
            # 删除游戏状态
            del self.game_states[user_id]
            
        except Exception as e:
            logger.error(f"[IdiomGame] 结束游戏异常：{str(e)}", exc_info=True)
            self._send_text_reply("结束游戏失败，请稍后再试", e_context)

    def _clear_history_leaderboard(self, e_context: EventContext):
        """清除历史排行榜"""
        try:
            # 获取用户昵称
            user_name = self._get_user_name(e_context)
            
            # 备份当前排行榜
            backup_file = os.path.join(os.path.dirname(__file__), f"leaderboard_backup_{int(time.time())}.json")
            try:
                with open(self.leaderboard_file, 'r', encoding='utf-8') as f:
                    current_data = f.read()
                with open(backup_file, 'w', encoding='utf-8') as f:
                    f.write(current_data)
                logger.info(f"[IdiomGame] 已备份排行榜数据到: {backup_file}")
            except Exception as e:
                logger.error(f"[IdiomGame] 备份排行榜数据失败: {str(e)}")
            
            # 清空排行榜数据
            self.leaderboard = {}
            self._save_leaderboard()
            
            # 清空当前轮次排行榜
            self.current_round_scores = {}
            
            # 发送确认消息
            confirm_message = (
                f"✨ 历史排行榜已清除\n"
                f"操作者：{user_name}\n"
                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💾 原数据已备份"
            )
            self._send_text_reply(confirm_message, e_context)
            
            logger.info(f"[IdiomGame] 历史排行榜已被管理员 {user_name} 清除")
            
        except Exception as e:
            logger.error(f"[IdiomGame] 清除历史排行榜异常: {str(e)}", exc_info=True)
            self._send_text_reply("清除历史排行榜失败，请稍后再试", e_context)

# OpenAI助手类
class OpenAIHelper:
    """OpenAI助手，用于生成成语提示和解释"""
    
    def __init__(self, api_key="", model="gpt-3.5-turbo", timeout=10, api_base="https://api.openai.com/v1"):
        """初始化OpenAI助手"""
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.api_base = api_base.rstrip('/')  # 确保没有尾部斜杠
        
        # 初始化缓存，避免重复请求
        self.explanation_cache = {}  # {idiom: explanation}
        self.hint_cache = {}  # {idiom: {level: hint}}
        self.lock = threading.Lock()
        
        # 检查API密钥是否有效
        if not api_key:
            logger.warning("[IdiomGame] OpenAI API密钥为空，将使用默认提示")
    
    def is_available(self):
        """检查OpenAI服务是否可用"""
        return bool(self.api_key)
    
    def get_idiom_hint(self, idiom, hint_level=1):
        """获取成语提示，不包含答案"""
        # 如果已缓存，直接返回
        with self.lock:
            if idiom in self.hint_cache and hint_level in self.hint_cache[idiom]:
                return self.hint_cache[idiom][hint_level]
        
        if not self.is_available():
            return ""
            
        try:
            # 根据提示级别调整提示内容
            system_prompt = "你是一个中国成语专家，擅长为猜成语游戏提供巧妙的提示，帮助玩家猜出成语，但不会直接给出答案。"
            user_prompt = ""
            
            if hint_level == 1:
                user_prompt = f"请为成语「{idiom}」提供一个巧妙的提示，帮助别人猜出这个成语。不要直接给出答案，而是从成语的含义、典故、用法等方面提供线索（30字以内）。不要有任何引导语，直接给出提示。"
            elif hint_level == 2:
                user_prompt = f"请为成语「{idiom}」提供一个更详细的提示，帮助别人猜出这个成语。可以从成语的字面意思、引申义、常见用法等方面提供更多线索（50字以内）。不要直接给出答案。"
            elif hint_level == 3:
                user_prompt = f"请为成语「{idiom}」提供一个更直接的提示，帮助别人猜出这个成语。可以解释成语的基本含义，但不要直接说出成语本身（70字以内）。"
            elif hint_level == 4:
                user_prompt = f"请为成语「{idiom}」提供一个典故相关的提示，帮助别人猜出这个成语。可以讲述成语的出处或典故，但不要直接说出成语本身（100字以内）。"
            
            # 构造请求
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 150
            }
            
            # 发送请求
            response = requests.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=self.timeout
            )
            
            # 处理响应
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and result["choices"]:
                    hint = result["choices"][0]["message"]["content"].strip()
                    hint = self._clean_response(hint)
                    
                    # 确保提示中不包含答案
                    if idiom in hint:
                        hint = re.sub(idiom, "**", hint)
                    
                    # 缓存结果
                    with self.lock:
                        if idiom not in self.hint_cache:
                            self.hint_cache[idiom] = {}
                        self.hint_cache[idiom][hint_level] = hint
                    
                    return hint
            
            logger.warning(f"[IdiomGame] OpenAI请求失败: {response.status_code} {response.text}")
            return ""
            
        except Exception as e:
            logger.error(f"[IdiomGame] 获取成语提示异常: {str(e)}", exc_info=True)
            return ""
    
    def get_idiom_explanation(self, idiom):
        """获取成语解释"""
        # 如果已缓存，直接返回
        with self.lock:
            if idiom in self.explanation_cache:
                return self.explanation_cache[idiom]
        
        if not self.is_available():
            return ""
            
        try:
            # 构造请求
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是一个中国成语专家，擅长解释成语的意思、出处和典故。请简洁明了地回答，不要有任何多余的修饰和引导语。"},
                    {"role": "user", "content": f"请简要解释成语「{idiom}」的意思、出处和典故（100字以内）。直接给出解释，不要有任何引导语。"}
                ],
                "temperature": 0.7,
                "max_tokens": 150
            }
            
            # 发送请求
            response = requests.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=self.timeout
            )
            
            # 处理响应
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and result["choices"]:
                    explanation = result["choices"][0]["message"]["content"].strip()
                    explanation = self._clean_response(explanation)
                    
                    # 缓存结果
                    with self.lock:
                        self.explanation_cache[idiom] = explanation
                    
                    return explanation
            
            logger.warning(f"[IdiomGame] OpenAI请求失败: {response.status_code} {response.text}")
            return ""
            
        except Exception as e:
            logger.error(f"[IdiomGame] 获取成语解释异常: {str(e)}", exc_info=True)
            return ""
    
    def _clean_response(self, text):
        """清理OpenAI响应，移除引导语等"""
        # 移除常见的引导语
        text = re.sub(r'^(好的|这个成语|这里是|以下是|成语)[,.，。:：]?\s*', '', text.strip())
        text = re.sub(r'^["「【《]\s*', '', text.strip())
        text = re.sub(r'\s*["」】》]$', '', text.strip())
        
        return text 
