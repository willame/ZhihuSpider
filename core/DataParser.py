import html
import json
import queue
import threading
import logging
from bs4 import BeautifulSoup
from core.Logger import log

# 知乎用户信息字段
# 用户头像
USER_AVATAR_URL_TEMPLATE = 'avatarUrlTemplate'
# 用户标识
USER_URL_TOKEN = 'urlToken'
# 用户名
USER_NAME = 'name'
# 用户自我介绍
USER_HEADLINE = 'headline'
# 用户居住地
USER_LOCATIONS = 'locations'
# 用户所在行业
USER_BUSINESS = 'business'
# 用户职业经历
USER_EMPLOYMENTS = 'employments'
# 用户教育经历
USER_EDUCATIONS = 'educations'
# 用户个人描述
USER_DESCRIPTION = 'description'
# 用户新浪微博 URL
USER_SINAWEIBO_URL = 'sinaWeiboUrl'
# 用户性别
USER_GENDER = 'gender'
# 正在关注用户的数目
USER_FOLLOWING_COUNT = 'followingCount'
# 关注者的数目
USER_FOLLOWER_COUNT = 'followerCount'
# 该用户回答问题的数目
USER_ANSWER_COUNT = 'answerCount'
# 用户提问数目
USER_QUESTION_COUNT = 'questionCount'
# 用户获得赞同的数目
USER_VOTE_UP_COUNT = 'voteupCount'

# JSON 数据关键字
JSON_ENTITIES = 'entities'
JSON_USERS = 'users'

# 队列中数据字典的键值
QUEUE_ELEM_HTML = 'html'
QUEUE_ELEM_TOKEN = 'token'
QUEUE_ELEM_THREAD_NAME = 'thread_name'

# 用户信息数据待解析缓存大小
USER_INFO_CACHE_QUEUE_SIZE = 300
# 用户列表数据待解析缓存大小
USER_LIST_CACHE_QUEUE_SIZE = 300


class CacheQueue:
    def __init__(self):
        self.user_info_cache_queue = queue.Queue(USER_INFO_CACHE_QUEUE_SIZE)
        self.user_list_cache_queue = queue.Queue(USER_LIST_CACHE_QUEUE_SIZE)

    # 添加一条待解析数据到 user_info_cache_queue 中
    def add_data_into_user_info_cache_queue(self, data):
        self.user_info_cache_queue.put(data)

    # 添加一条待解析数据到 user_list_cache_queue 中
    def add_data_into_user_list_cache_queue(self, data):
        self.user_list_cache_queue.put(data)

    # 从 user_info_cache_queue 中获取一条数据
    def get_data_from_user_info_cache_queue(self):
        return self.user_info_cache_queue.get()

    # 从 user_list_cache_queue 中获取一条数据
    def get_data_from_user_list_cache_queue(self):
        return self.user_list_cache_queue.get()


# 数据解析模块
class DataParseModule:
    def __init__(self, db_connection, user_token_cache_queue, cache_queue, bloom_filter):
        self.db_connection = db_connection
        self.user_token_cache_queue = user_token_cache_queue
        self.cache_queue = cache_queue
        self.bloom_filter = bloom_filter
        self.user_info_data_parse_thread = UserInfoDataParserThread(self.db_connection,
                                                                    self.user_token_cache_queue,
                                                                    self.cache_queue,
                                                                    self.bloom_filter)
        self.user_list_data_parse_thread = UserListDataParserThread(self.db_connection,
                                                                    self.user_token_cache_queue, self.cache_queue)

    # 启动用户信息数据解析线程
    def start_user_info_data_parse_thread(self):
        self.user_info_data_parse_thread.start()
        if log.isEnabledFor(logging.DEBUG):
            log.debug("用户信息数据解析线程启动")

    # 启动用户列表数据解析线程
    def start_user_list_data_parse_thread(self):
        self.user_list_data_parse_thread.start()
        if log.isEnabledFor(logging.DEBUG):
            log.debug('用户列表数据解析线程启动')

    def get_user_info_data_parse_thread_status(self):
        return self.user_info_data_parse_thread.status

    def get_user_list_data_parse_thread_status(self):
        return self.user_list_data_parse_thread.status

    def restart_user_info_data_parse_thread(self):
        self.user_info_data_parse_thread = UserInfoDataParserThread(self.db_connection,
                                                                    self.user_token_cache_queue,
                                                                    self.cache_queue,
                                                                    self.bloom_filter)
        self.start_user_info_data_parse_thread()

    def restart_user_list_data_parse_thread(self):
        self.user_list_data_parse_thread = UserListDataParserThread(self.db_connection,
                                                                    self.user_token_cache_queue, self.cache_queue)
        self.start_user_list_data_parse_thread()


# 用户信息数据解析线程
class UserInfoDataParserThread(threading.Thread):
    def __init__(self, db_connection, user_token_cache_queue, cache_queue, bloom_filter):
        threading.Thread.__init__(self)
        self.user_token_cache_queue = user_token_cache_queue
        self.cache_queue = cache_queue
        self.db_connection = db_connection
        self.bloom_filter = bloom_filter
        self.status = 'running'

    def run(self):
        try:
            while True:
                raw_data = None
                token = None
                thread_name = None

                data = self.cache_queue.get_data_from_user_info_cache_queue()

                if QUEUE_ELEM_HTML in data:
                    raw_data = data[QUEUE_ELEM_HTML]
                if QUEUE_ELEM_TOKEN in data:
                    token = data[QUEUE_ELEM_TOKEN]
                if QUEUE_ELEM_THREAD_NAME in data:
                    thread_name = data[QUEUE_ELEM_THREAD_NAME]

                if raw_data is not None and token is not None:
                    user_info = self.parse_user_information(raw_data, token)
                    if user_info is not None:
                        if log.isEnabledFor(logging.DEBUG):
                            log.debug('[' + thread_name + "]搜索到一个用户:" + user_info[USER_NAME])
                        # 使用布隆过滤器标记
                        self.bloom_filter.mark_value(user_info[USER_URL_TOKEN])
                        self.db_connection.add_user_info(self.convert_user_info(user_info))
                        # 封装代分析用户关注列表的token信息
                        token_info = {USER_URL_TOKEN: user_info[USER_URL_TOKEN],
                                      USER_FOLLOWING_COUNT: user_info[USER_FOLLOWING_COUNT],
                                      USER_FOLLOWER_COUNT: user_info[USER_FOLLOWER_COUNT]}
                        # print(token_info)
                        self.user_token_cache_queue.add_token_into_analysed_cache_queue([token_info])
        except Exception as e:
            if log.isEnabledFor(logging.ERROR):
                log.exception(e)
            self.status = 'error'

    # 解析 html 中的知乎用户信息
    @staticmethod
    def parse_user_information(html_string, user_token):
        if html_string is None:
            return None
        # 提取 json 数据
        bs_object = BeautifulSoup(html_string, 'html.parser')
        data_string = bs_object.find('div', attrs={'id': 'data'})
        if data_string is None:
            return None
        else:
            data_string = data_string['data-state']

        # 字符串处理
        # 对转义 html 字符进行处理
        data_string = html.unescape(data_string)
        # 去除夹杂的 html 标签
        data_string = BeautifulSoup(data_string, 'html.parser').text
        # 转换为 json 对象
        try:
            # 防止解析到的 JSON 格式错误而引发异常
            json_data = json.loads(data_string)
        except ValueError:
            if log.isEnabledFor(logging.DEBUG):
                log.debug('[error]解析到错误的 json 数据')
            return None

        # 提取实体
        if JSON_ENTITIES not in json_data:
            return None
        entities = json_data[JSON_ENTITIES]

        # 提取各个用户信息
        if JSON_USERS not in entities:
            return None
        users = entities[JSON_USERS]

        # 提取目标用户
        if user_token not in users:
            return None
        user = users[user_token]

        # 提取目标用户的个人信息
        avatar_url_template = None
        url_token = None
        name = None
        headline = None
        locations = []
        business = None
        employments = []
        educations = []
        description = None
        sina_weibo_url = None
        gender = None
        following_count = None
        follower_count = None
        answer_count = None
        question_count = None
        voteup_count = None

        if USER_AVATAR_URL_TEMPLATE in user:
            avatar_url_template = user[USER_AVATAR_URL_TEMPLATE]

        if USER_URL_TOKEN in user:
            url_token = user[USER_URL_TOKEN]

        if USER_NAME in user:
            name = user[USER_NAME]

        if USER_HEADLINE in user:
            headline = user[USER_HEADLINE]

        if USER_LOCATIONS in user:
            for location in user[USER_LOCATIONS]:
                locations.append(location['name'])

        if USER_BUSINESS in user:
            business = user[USER_BUSINESS]['name']

        if USER_EMPLOYMENTS in user:
            for employment in user[USER_EMPLOYMENTS]:
                elem = {}
                if 'job' in employment:
                    job = employment['job']['name']
                    elem.update({'job': job})
                if 'company' in employment:
                    company = employment['company']['name']
                    elem.update({'company': company})
                employments.append(elem)

        if USER_EDUCATIONS in user:
            for education in user[USER_EDUCATIONS]:
                if 'school' in education:
                    school = education['school']['name']
                    educations.append(school)

        if USER_DESCRIPTION in user:
            description = user[USER_DESCRIPTION]

        if USER_SINAWEIBO_URL in user:
            sina_weibo_url = user[USER_SINAWEIBO_URL]

        if USER_GENDER in user:
            gender = user[USER_GENDER]

        if USER_FOLLOWING_COUNT in user:
            following_count = user[USER_FOLLOWING_COUNT]

        if USER_FOLLOWER_COUNT in user:
            follower_count = user[USER_FOLLOWER_COUNT]

        if USER_ANSWER_COUNT in user:
            answer_count = user[USER_ANSWER_COUNT]

        if USER_QUESTION_COUNT in user:
            question_count = user[USER_QUESTION_COUNT]

        if USER_VOTE_UP_COUNT in user:
            voteup_count = user[USER_VOTE_UP_COUNT]

        # 构造用户信息实体
        user_info = {USER_AVATAR_URL_TEMPLATE: avatar_url_template,
                     USER_URL_TOKEN: url_token,
                     USER_NAME: name,
                     USER_HEADLINE: headline,
                     USER_LOCATIONS: locations,
                     USER_BUSINESS: business,
                     USER_EMPLOYMENTS: employments,
                     USER_EDUCATIONS: educations,
                     USER_DESCRIPTION: description,
                     USER_SINAWEIBO_URL: sina_weibo_url,
                     USER_GENDER: gender,
                     USER_FOLLOWING_COUNT: following_count,
                     USER_FOLLOWER_COUNT: follower_count,
                     USER_ANSWER_COUNT: answer_count,
                     USER_QUESTION_COUNT: question_count,
                     USER_VOTE_UP_COUNT: voteup_count}
        return user_info

        # 转换该用户信息实体为可保存的数据库格式

    @staticmethod
    def convert_user_info(user_info):
        # 将居住地转换为‘；’分隔的字符串
        locations_string = ';'.join(str(x) for x in user_info[USER_LOCATIONS])
        user_info[USER_LOCATIONS] = locations_string

        # 将职业经历转换为‘XXX（XXX）’，并以‘；’ 分隔的字符串
        employments_list = []
        for employment in user_info[USER_EMPLOYMENTS]:
            temp = ''
            if 'company' in employment:
                temp += str(employment['company'])
            if 'job' in employment:
                temp += '-' + str(employment['job'])
            employments_list.append(temp)
        employments_string = ';'.join(str(x) for x in employments_list)
        user_info[USER_EMPLOYMENTS] = employments_string

        # 将教育经历转换为‘；’分隔的字符串
        educations_string = ';'.join(str(x) for x in user_info[USER_EDUCATIONS])
        user_info[USER_EDUCATIONS] = educations_string

        return user_info


# 用户列表数据分析线程
class UserListDataParserThread(threading.Thread):
    def __init__(self, db_connection, user_token_cache_queue, cache_queue):
        threading.Thread.__init__(self)
        self.cache_queue = cache_queue
        self.user_token_cache_queue = user_token_cache_queue
        self.db_connection = db_connection
        self.status = 'running'

    def run(self):
        try:
            while True:
                raw_data = None
                token = None
                thread_name = None

                data = self.cache_queue.get_data_from_user_list_cache_queue()
                if QUEUE_ELEM_HTML in data:
                    raw_data = data[QUEUE_ELEM_HTML]
                if QUEUE_ELEM_TOKEN in data:
                    token = data[QUEUE_ELEM_TOKEN]
                if QUEUE_ELEM_THREAD_NAME in data:
                    thread_name = data[QUEUE_ELEM_THREAD_NAME]

                if raw_data is not None and token is not None:
                    token_list = self.parse_user_list(raw_data, token)
                    if token_list is not None:
                        if log.isEnabledFor(logging.DEBUG):
                            log.debug('[' + thread_name + ']开始分析用户“' + token + '”的关注列表')
                        self.user_token_cache_queue.add_token_into_cache_queue(token_list)
        except Exception as e:
            if log.isEnabledFor(logging.ERROR):
                log.exception(e)
            self.status = 'error'

    # 解析 html 中的用户列表
    @staticmethod
    def parse_user_list(html_string, user_token):
        if html_string is None:
            return None

        # 保存提取到的用户 url token
        user_token_list = []

        # 提取 json 数据
        bs_object = BeautifulSoup(html_string, 'html.parser')
        data_string = bs_object.find('div', attrs={'id': 'data'})
        if data_string is None:
            return None
        else:
            data_string = data_string['data-state']

        # 字符串处理
        # 对转义 html 字符进行处理
        data_string = html.unescape(data_string)
        # 去除夹杂的 html 标签
        data_string = BeautifulSoup(data_string, 'html.parser').text
        # 转换为 json 对象
        json_data = json.loads(data_string)

        # 提取实体
        if JSON_ENTITIES not in json_data:
            return None
        entities = json_data[JSON_ENTITIES]

        # 提取用户列表信息
        if JSON_USERS not in entities:
            return None
        users = entities[JSON_USERS]

        # 提取用户 token
        for token in users.keys():
            if token != user_token:
                user_token_list.append(token)

        return user_token_list
