import _thread as thread
import base64
import hashlib
import hmac
import json
import ssl
import sys
import time
from datetime import datetime
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import websocket
import xmltodict


class WebsocketReq(object):

    def __init__(self, appid, apisecret, apikey, audio_file, text, category, ent):
        # appid, apisecret, apikey 可在控制台获取
        self.APPID = appid
        self.APISecret = apisecret
        self.APIKey = apikey
        self.url_prefix = 'wss://ise-api.xfyun.cn/v2/open-ise?'  # 固定url前缀
        self.status = 'param_upload'  # 定义初始状态为参数上传阶段
        self.audio_file = audio_file  # 音频文件路径
        self.text = text  # 待评测的文本
        self.category = category  # 待评测的题型
        self.ent = ent  # 带评测的语种
        self.ws = None
        self.result = {}

    def gen_time(self):
        """生成RFC1123格式的时间戳"""
        now_time = datetime.now()
        date = format_date_time(mktime(now_time.timetuple()))
        return date

    def gen_url(self):
        """生成鉴权url"""
        date = self.gen_time()
        signature_origin = "host: " + "ise-api.xfyun.cn" + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + "/v2/open-ise " + "HTTP/1.1"
        signature_sha = hmac.new(self.APISecret.encode('utf-8'), signature_origin.encode('utf-8'),
                                 digestmod=hashlib.sha256).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')

        authorization_origin = "api_key=\"%s\", algorithm=\"%s\", headers=\"%s\", signature=\"%s\"" % (
            self.APIKey, "hmac-sha256", "host date request-line", signature_sha)
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
        v = {
            "authorization": authorization,
            "date": date,
            "host": "ise-api.xfyun.cn"
        }
        return self.url_prefix + urlencode(v)

    def gen_req_param(self, status, data=None):
        """生成不同阶段请求参数,一些个性化参数也可以在这里修改"""
        # 第一次参数上传阶段
        if status == 'param_upload':
            params = {"common": {"app_id": self.APPID},
                      "business": {"category": self.category, 'rstcd': 'utf8', "cver": "1.0", "group": "pupil",
                                   "sub": "ise", "ent": self.ent, "tte": "utf-8", "cmd": "ssb", "ssm": "1",
                                   "auf": "audio/L16;rate=16000", "aue": "raw",
                                   "text": '\uFEFF' + self.text},
                      "data": {"status": 0, "data": ""}}
        # 第二次首次音频数据上传阶段
        elif status == 'data_first':
            params = {"business": {"cmd": "auw", "aus": 1, "aue": "raw"},
                      "data": {"status": 1, "data": base64.b64encode(data).decode('utf-8'), "data_type": 1,
                               "encoding": "raw"}}
        # 中间音频上传阶段
        elif status == 'data_mid':
            params = {"business": {"cmd": "auw", "aus": 2, "aue": "raw"},
                      "data": {"status": 1, "data": base64.b64encode(data).decode('utf-8'),
                               "data_type": 1, "encoding": "raw"}}
        # 最后一帧参数
        elif status == 'param_last':
            params = {"business": {"cmd": "auw", "aus": 4, "aue": "raw"},
                      "data": {"status": 2, "data": "", "data_type": 1, "encoding": "raw"}}
        return json.dumps(params)

    #@staticmethod
    def on_message(self, message):
        #print(message)
        #sid = json.loads(message)["sid"]
        code = json.loads(message)["code"]
        #print(sid)
        if code != 0:
            print('请求错误:', message)
            return
        status = json.loads(message)['data']['status']
        if status == 2:
            #print('请求成功')
            #print('原始的xml结果:')
            data = base64.b64decode(json.loads(message)['data']['data'])
            data = data.decode('utf-8')
            dic = xmltodict.parse(data)
            dic = json.dumps(dic, indent=4)
            dic2 = json.loads(dic)

            f = open("daneil.txt","w")
            f.write(dic)
            f.close()
            self.result['句子精准度'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['@accuracy_score']
            self.result['句子内容'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['@content']
            self.result['句子流畅度'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['fluency_score']
            self.result['句子标准度'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['@standard_score']
            self.result['句子完整度'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['@integrity_score']
            self.result['句子总得分'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['@total_score']
            self.result['单词'] = data['xml_result']['read_sentence']['rec_paper']['read_chapter']['word']
            print(self.result['单词'])
            time.sleep(1)
            self.ws.close()
            #print("thread terminating...")


    #@staticmethod
    def on_error(self, error):
        print(error)

    #@staticmethod
    def on_close(self,ws):
        print("### closed ###")

    #@staticmethod
    def on_open(self):
        def run(*args):
            """发送的status先后顺序: param_upload, data_first, data_mid, param_last"""
            # 先进行参数
            self.ws.send(self.gen_req_param('param_upload'))
            # 定义一个初始发送状态
            status = 'data_first'
            # 开始读取音频进行数据上传
            with open(self.audio_file, 'rb') as f:
                while True:
                    data = f.read(9000)
                    if not data:
                        # 没有数据了，更改状态为LAST
                        self.ws.send(self.gen_req_param('param_last'))
                        break
                    # 根据data来判断当前状态来发送不同的内容
                    if status == 'data_first':
                        self.ws.send(self.gen_req_param(status, data))
                        # 修改发送状态为发送中间音频
                        status = 'data_mid'
                    if status == 'data_mid':
                        self.ws.send(self.gen_req_param(status, data))
        thread.start_new_thread(run, ())

    def upload(self):
        self.ws = websocket.WebSocketApp(url=self.gen_url(),
                                on_message=self.on_message,
                                on_error=self.on_error,
                                on_close=self.on_close)
        self.ws.on_open = self.on_open
        self.ws.run_forever()
        return self.result
        # ret = websocket_instance.pop(ws)
        # return ret

def test1():

    WebsocketReq1 = WebsocketReq(
        appid='5f20ecf1',
        apisecret='ea49d6aca557c416e7e2fd8f66ac98b6',
        apikey='e0b76912f87ee883e5be737b6d83681b',
        audio_file=r'D:\xf\学生语音测试音频\01 Week 1 Lesson 1 Speaking - Daniel Vocabulary.wav',
        text='good morning,good night,good afternoon,good evening,name,fine',
        category='read_sentence',
        ent='en_vip')
    websocket.enableTrace(False)
    data = WebsocketReq1.upload()
    print(data)

test1()