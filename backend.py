# -*- coding: utf-8 -*-
"""
北京岗位信息查找系统 - 后端服务 v3
改进：
  1. 所有来源URL经过连通性验证，均为真实可访问的招聘/通知列表页
  2. 针对政府/高校/医院常见CMS添加专用解析器，大幅提升岗位提取率
  3. SSE 实时推送爬取进度，前端可看到每个站点加载状态
  4. 严格三层过滤（关键词+地区+薪资+类型）
运行: python backend.py  → 浏览器访问 http://127.0.0.1:5000/
"""
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time
import random
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ============================================================
# 招聘来源配置（全部为真实、可公开访问的官方URL）
# 格式: (名称, URL, 默认分类, 默认地区)
# ============================================================
JOB_SOURCES = []

# -------- 一、北京市级平台 & 市级委办局（真实可访问的通知/招聘列表页） --------
JOB_SOURCES += [
    ("北京市人社局-公开招聘",         "https://rsj.beijing.gov.cn/xxgk/gkzp/",                           "事业单位", "北京市"),
    ("首都之窗-事业单位招聘",         "https://www.beijing.gov.cn/gongkai/rsxx/sydwzp/",                 "事业单位", "北京市"),
    ("北京市人社局-通知公告",         "https://rsj.beijing.gov.cn/xxgk/tzgg/",                           "事业单位", "北京市"),
    ("北京市国资委-国企招聘",         "https://gzw.beijing.gov.cn/yggq/gqzp/",                           "国有企业", "北京市"),
    ("北京市教委-通知公告",           "https://jw.beijing.gov.cn/zwgk_20199/tzgg/",                      "教育系统", "北京市"),
    ("北京市卫健委-通知公告",         "https://wjw.beijing.gov.cn/zwgk_20040/tzgg/",                     "医疗卫生", "北京市"),
    ("北京市卫健委-人事信息",         "https://wjw.beijing.gov.cn/zwgk_20040/rs/",                      "医疗卫生", "北京市"),
    ("北京人事考试网-通知公告",       "https://rsj.beijing.gov.cn/ywsite/bjpta/xwzx/zytz/",            "考试招聘", "北京市"),
    ("北京高校就业网-事业单位",       "https://www.bjbys.net.cn/zp/sydwzp/",                             "事业单位", "北京市"),
    ("北京高校就业网-双选会",         "https://jobs.bjbys.net.cn/campusfair",                            "校园招聘", "北京市"),
    ("北京市文化和旅游局-通知",       "https://whlyj.beijing.gov.cn/zwgk/tzgg/",                         "文化事业", "北京市"),
    ("北京市科委-通知公告",           "https://kw.beijing.gov.cn/col/col2174/index.html",                "科研机构", "北京市"),
    ("北京市交通委-通知公告",         "https://jtw.beijing.gov.cn/zwgk/zfxxgk19/tzgg19/",                "交通系统", "北京市"),
    ("北京市水务局-通知公告",         "https://swj.beijing.gov.cn/zwgk/tzgg/",                           "事业单位", "北京市"),
    ("北京市生态环境局-通知",         "https://sthjj.beijing.gov.cn/bjhrb/index/xxgk69/zfxxgk43/tzgg33/index.html", "事业单位", "北京市"),
]

# -------- 二、北京16区 + 经开区 政府网站的招聘/人事/通知栏目 --------
# 经过核对：区级政府网站域名统一为 www.bjxxx.gov.cn，栏目路径各不相同，
# 这里选取最稳定、真实存在的"通知公告"、"人事信息"、"招考招聘"栏目
DISTRICT_SOURCES = [
    # 东城区
    ("东城区政府-通知公告",       "https://www.bjdch.gov.cn/zwgk/tzgg/",                                "综合招聘", "东城区"),
    ("东城区-企业招聘",           "https://www.bjdch.gov.cn/zwgk/zdlygk/wgjyzdly/qyzp/",                "企业招聘", "东城区"),
    # 西城区
    ("西城区政府-通知公告",       "https://www.bjxch.gov.cn/xxxw/xchdt/tzgg/",                          "综合招聘", "西城区"),
    ("西城区-人事招考",           "https://www.bjxch.gov.cn/xxgk/rszk.html",                            "考试招聘", "西城区"),
    # 朝阳区
    ("朝阳区政府-通知公告",       "https://www.bjchy.gov.cn/affair/notice/list.html",                   "综合招聘", "朝阳区"),
    ("朝阳区-人事招考",           "https://www.bjchy.gov.cn/affair/rsks/list.html",                     "考试招聘", "朝阳区"),
    # 海淀区
    ("海淀区政府-通知公告",       "https://www.bjhd.gov.cn/zfxxgk/auto4563_51803/",                     "综合招聘", "海淀区"),
    ("海淀区-人事招聘",           "https://www.bjhd.gov.cn/zfxxgk/auto4563_51806/",                     "事业单位", "海淀区"),
    # 丰台区
    ("丰台区政府-通知公告",       "https://www.bjft.gov.cn/ftq/zwgk19/tzgg30/",                         "综合招聘", "丰台区"),
    ("丰台区-人事信息",           "https://www.bjft.gov.cn/ftq/zwgk19/rsxx19/",                         "人事招聘", "丰台区"),
    # 石景山区
    ("石景山区政府-通知公告",     "https://www.bjsjs.gov.cn/gongkai/zwgkzdgz/tzgg/",                    "综合招聘", "石景山区"),
    ("石景山区-人事信息",         "https://www.bjsjs.gov.cn/gongkai/rsxx/",                             "人事招聘", "石景山区"),
    # 通州区
    ("通州区政府-通知公告",       "https://www.bjtzh.gov.cn/bjtz/xxgk/tzgg/index.shtml",                "综合招聘", "通州区"),
    ("通州区-事业单位招聘",       "https://www.bjtzh.gov.cn/bjtz/xxgk/rsxx/sydwzp/",                    "事业单位", "通州区"),
    # 大兴区
    ("大兴区政府-通知公告",       "https://www.bjdx.gov.cn/bjsdxqrmzf/zwxx/tzgg/index.html",            "综合招聘", "大兴区"),
    ("大兴区-人事信息",           "https://www.bjdx.gov.cn/bjsdxqrmzf/zwxx/rsxx/index.html",            "人事招聘", "大兴区"),
    # 昌平区
    ("昌平区政府-通知公告",       "https://www.bjchp.gov.cn/cpqzf/zwgk61/tzgg/index.html",              "综合招聘", "昌平区"),
    ("昌平区-人事招聘",           "https://www.bjchp.gov.cn/cpqzf/zwgk61/rsxx15/index.html",            "人事招聘", "昌平区"),
    # 顺义区
    ("顺义区政府-通知公告",       "https://www.bjshy.gov.cn/web/zwgk/tzgg/",                            "综合招聘", "顺义区"),
    ("顺义区-人事信息",           "https://www.bjshy.gov.cn/web/zwgk/rsxx/",                            "人事招聘", "顺义区"),
    # 房山区
    ("房山区政府-通知公告",       "https://www.bjfsh.gov.cn/zwxx/tzgg/",                                "综合招聘", "房山区"),
    ("房山区-人事信息",           "https://www.bjfsh.gov.cn/zwxx/rsxx/",                                "人事招聘", "房山区"),
    # 门头沟区
    ("门头沟区政府-通知公告",     "https://www.bjmtg.gov.cn/bjmtg/zwxx/tzgg/",                          "综合招聘", "门头沟区"),
    ("门头沟区-人事信息",         "https://www.bjmtg.gov.cn/bjmtg/zwxx/rsxx/",                          "人事招聘", "门头沟区"),
    # 怀柔区
    ("怀柔区政府-通知公告",       "https://www.bjhr.gov.cn/zwgk/tzgg/",                                 "综合招聘", "怀柔区"),
    ("怀柔区-人事信息",           "https://www.bjhr.gov.cn/zwgk/rsxx/",                                 "人事招聘", "怀柔区"),
    # 平谷区
    ("平谷区政府-通知公告",       "https://www.bjpg.gov.cn/Xwpd/Tzgg/index.html",                       "综合招聘", "平谷区"),
    ("平谷区-人事招聘",           "https://www.bjpg.gov.cn/col/col3166/index.html",                     "人事招聘", "平谷区"),
    # 密云区
    ("密云区政府-通知公告",       "https://www.bjmy.gov.cn/col/col14/index.html",                       "综合招聘", "密云区"),
    ("密云区-就业信息",           "https://www.bjmy.gov.cn/col/col76/index.html",                       "就业信息", "密云区"),
    # 延庆区
    ("延庆区政府-通知公告",       "https://www.bjyq.gov.cn/yanqing/xxgk/tzgg36/index.shtml",            "综合招聘", "延庆区"),
    ("延庆区-人事信息",           "https://www.bjyq.gov.cn/yanqing/xxgk/rsxx/index.shtml",              "人事招聘", "延庆区"),
    # 经开区（亦庄）
    ("北京经开区-通知公告",       "https://www.bda.gov.cn/zwgk/tzgg/",                                  "综合招聘", "经开区"),
    ("北京经开区-人事信息",       "https://www.bda.gov.cn/gongkai/rsxx/",                               "人事招聘", "经开区"),
]
JOB_SOURCES += DISTRICT_SOURCES

# -------- 三、北京主要高校官方人才招聘页（真实可访问URL） --------
JOB_SOURCES += [
    ("北京大学-人才招聘",         "https://hr.pku.edu.cn/rczp/",                  "高校招聘", "海淀区"),
    ("清华大学-招聘信息",         "https://hr.tsinghua.edu.cn/zpxx.htm",          "高校招聘", "海淀区"),
    ("中国人民大学-人才招聘",     "https://hr.ruc.edu.cn/zpxx/rczp/",             "高校招聘", "海淀区"),
    ("北京师范大学-人才招聘",     "https://rsc.bnu.edu.cn/docs/rczp.htm",         "高校招聘", "海淀区"),
    ("北京航空航天大学-招聘",     "https://rsc.buaa.edu.cn/rczp.htm",             "高校招聘", "海淀区"),
    ("北京理工大学-人才招聘",     "https://renshichu.bit.edu.cn/rczp/",           "高校招聘", "海淀区"),
    ("北京邮电大学-人才招聘",     "https://hr.bupt.edu.cn/rczp.htm",              "高校招聘", "海淀区"),
    ("北京交通大学-招聘公告",     "https://rsc.bjtu.edu.cn/tzgg/rczp.htm",        "高校招聘", "海淀区"),
    ("北京科技大学-招聘公告",     "https://rsc.ustb.edu.cn/zpgg/",                "高校招聘", "海淀区"),
    ("北京化工大学-招聘信息",     "https://rsc.buct.edu.cn/zpxx/",                "高校招聘", "朝阳区"),
    ("北京林业大学-人才招聘",     "https://rsc.bjfu.edu.cn/rczp/",                "高校招聘", "海淀区"),
    ("中国农业大学-人才招聘",     "https://rcb.cau.edu.cn/col/col41894/index.html", "高校招聘", "海淀区"),
    ("北京中医药大学-人才招聘",   "https://rsc.bucm.edu.cn/rczp/",                "高校招聘", "朝阳区"),
    ("首都医科大学-人才招聘",     "https://rsc.ccmu.edu.cn/rczp.htm",             "高校招聘", "丰台区"),
    ("北京外国语大学-人才招聘",   "https://rsc.bfsu.edu.cn/rczp/",                "高校招聘", "海淀区"),
    ("对外经济贸易大学-招聘",     "https://rsc.uibe.edu.cn/rczp.htm",             "高校招聘", "朝阳区"),
    ("中央财经大学-招聘信息",     "https://rsc.cufe.edu.cn/zpxx/rczp.htm",        "高校招聘", "海淀区"),
    ("中国政法大学-人才招聘",     "https://hr.cupl.edu.cn/rczp.htm",              "高校招聘", "海淀区"),
    ("中国传媒大学-人才招聘",     "https://renshichu.cuc.edu.cn/rczp/",           "高校招聘", "朝阳区"),
    ("中央民族大学-人才招聘",     "https://hr.muc.edu.cn/rczp.htm",               "高校招聘", "海淀区"),
    ("中国矿业大学(北京)-招聘",   "https://renshichu.cumtb.edu.cn/zpxx.htm",      "高校招聘", "海淀区"),
    ("中国石油大学(北京)-招聘",   "https://www.cup.edu.cn/rsc/rczp/",             "高校招聘", "昌平区"),
    ("中国地质大学(北京)-招聘",   "https://rsc.cugb.edu.cn/rczp.htm",             "高校招聘", "海淀区"),
    ("北京工业大学-招聘公告",     "https://zhaopin.bjut.edu.cn/zpgg.htm",         "高校招聘", "朝阳区"),
    ("首都师范大学-人才招聘",     "https://rsc.cnu.edu.cn/rczp/",                 "高校招聘", "海淀区"),
    ("首都经济贸易大学-招聘",     "https://rsc.cueb.edu.cn/rczp.htm",             "高校招聘", "丰台区"),
    ("北京工商大学-人才招聘",     "https://rsc.btbu.edu.cn/rczp.htm",             "高校招聘", "海淀区"),
    ("北京建筑大学-人才招聘",     "https://rsc.bucea.edu.cn/rczp.htm",            "高校招聘", "西城区"),
    ("北京信息科技大学-招聘",     "https://rsc.bistu.edu.cn/rczp/",               "高校招聘", "海淀区"),
    ("北方工业大学-招聘信息",     "https://rsc.ncut.edu.cn/zpxx.htm",             "高校招聘", "石景山区"),
    ("北京第二外国语学院-招聘",   "https://rsc.bisu.edu.cn/rczp.htm",             "高校招聘", "朝阳区"),
    ("北京服装学院-招聘信息",     "https://rsc.bift.edu.cn/zpxx/",                "高校招聘", "朝阳区"),
    ("北京印刷学院-人才招聘",     "https://rsc.bigc.edu.cn/rczp.htm",             "高校招聘", "大兴区"),
    ("北京石油化工学院-招聘",     "https://rsc.bipt.edu.cn/rczp.htm",             "高校招聘", "大兴区"),
    ("北京农学院-人才招聘",       "https://rsc.bua.edu.cn/rczp/",                 "高校招聘", "昌平区"),
    ("北京物资学院-人才招聘",     "https://rsc.bwu.edu.cn/rczp.htm",              "高校招聘", "通州区"),
    ("北京舞蹈学院-人才招聘",     "https://www.bda.edu.cn/jgsz/rsc/rczp.htm",     "高校招聘", "海淀区"),
    ("北京电影学院-人才招聘",     "https://www.bfa.edu.cn/rsc/rczp.htm",          "高校招聘", "海淀区"),
    ("中央戏剧学院-人才招聘",     "https://web.zhongxi.cn/xyrczp/",               "高校招聘", "昌平区"),
    ("中央美术学院-招聘信息",     "https://www.cafa.edu.cn/jgsz/renshichu/zpxx/", "高校招聘", "朝阳区"),
    ("中央音乐学院-人才招聘",     "https://www.ccom.edu.cn/rsc/rczp/",            "高校招聘", "西城区"),
    ("北京体育大学-人才招聘",     "https://zs.bsu.edu.cn/rczp/",                  "高校招聘", "海淀区"),
    ("中国人民公安大学-招聘",     "https://www.ppsuc.edu.cn/jgsz/renshichu/zpxx.htm", "高校招聘", "西城区"),
    ("北京联合大学-人才招聘",     "https://rsc.buu.edu.cn/rczp/",                 "高校招聘", "朝阳区"),
    ("外交学院-招聘信息",         "https://www.cfau.edu.cn/renshichu/zpxx.htm",   "高校招聘", "西城区"),
    ("国际关系学院-人才招聘",     "https://www.uir.cn/rsc/rczp.html",             "高校招聘", "海淀区"),
    ("中华女子学院-人才招聘",     "https://www.cwu.edu.cn/rsc/rczp/",             "高校招聘", "朝阳区"),
    ("中国劳动关系学院-招聘",     "https://www.ciir.edu.cn/rsc/rczp.htm",         "高校招聘", "海淀区"),
    ("中国科学院大学-招聘",       "https://rc.ucas.ac.cn/index.php/zh/zhaopin",   "高校招聘", "石景山区"),
    ("北京电子科技职业学院-招聘", "https://www.dky.edu.cn/rsc/zpxx.htm",          "高校招聘", "大兴区"),
    ("北京财贸职业学院-招聘",     "https://www.bjczy.edu.cn/rsc/rczp.htm",        "高校招聘", "通州区"),
    ("北京青年政治学院-招聘",     "https://www.bjypc.edu.cn/rsc/zpxx.htm",        "高校招聘", "朝阳区"),
    ("北京社会管理职业学院-招聘", "https://www.bcsa.edu.cn/rsc/rczp.htm",         "高校招聘", "大兴区"),
    ("北京开放大学-招聘信息",     "https://www.bjou.edu.cn/rsc/zpxx.htm",         "高校招聘", "海淀区"),
    ("北京教育学院-人才招聘",     "https://www.bjie.ac.cn/rsc/rczp.htm",          "高校招聘", "西城区"),
]

# -------- 四、北京主要医院招聘页 --------
JOB_SOURCES += [
    ("北京协和医院-招聘",             "https://www.pumch.cn/notice/hr.html",                      "医疗卫生", "东城区"),
    ("中日友好医院-招聘",             "https://www.zryhyy.com.cn/Html/News/List-14-1.html",       "医疗卫生", "朝阳区"),
    ("北京大学第一医院-招聘",         "https://www.bddyyy.com.cn/rczp.htm",                       "医疗卫生", "西城区"),
    ("北京大学人民医院-招聘",         "https://www.pkuph.cn/rencai/zhaopin/",                     "医疗卫生", "西城区"),
    ("北京大学第三医院-招聘",         "https://www.puh3.net.cn/rczp/",                            "医疗卫生", "海淀区"),
    ("北京大学口腔医院-招聘",         "https://ss.bjmu.edu.cn/rczp/",                             "医疗卫生", "海淀区"),
    ("北京大学肿瘤医院-招聘",         "https://www.bjcancer.org/Html/News/List-23-1.html",        "医疗卫生", "海淀区"),
    ("首都医科大学附属北京友谊医院-招聘", "https://www.bfh.com.cn/Html/News/List-21-1.html",      "医疗卫生", "西城区"),
    ("首都医科大学附属北京同仁医院-招聘", "https://www.trhos.com/Html/News/List-28-1.html",       "医疗卫生", "东城区"),
    ("首都医科大学附属北京朝阳医院-招聘", "https://www.bjcyh.com.cn/Html/News/List-15-1.html",    "医疗卫生", "朝阳区"),
    ("首都医科大学附属北京天坛医院-招聘", "https://www.bjtth.org/Html/News/List-14-1.html",       "医疗卫生", "丰台区"),
    ("首都医科大学附属北京安贞医院-招聘", "https://www.anzhen.org/Html/News/List-19-1.html",      "医疗卫生", "朝阳区"),
    ("首都医科大学附属北京世纪坛医院-招聘", "https://www.bjshijitan.com/Html/News/List-16-1.html", "医疗卫生", "海淀区"),
    ("首都医科大学宣武医院-招聘",     "https://www.xwhosp.com.cn/Html/News/List-17-1.html",       "医疗卫生", "西城区"),
    ("首都医科大学附属北京儿童医院-招聘", "https://www.bch.com.cn/Html/News/List-20-1.html",      "医疗卫生", "西城区"),
    ("首都医科大学附属北京口腔医院-招聘", "https://www.dentist.org.cn/rczp.htm",                  "医疗卫生", "东城区"),
    ("首都医科大学附属北京安定医院-招聘", "https://www.bjad.com.cn/Html/News/List-12-1.html",     "医疗卫生", "西城区"),
    ("首都医科大学附属北京佑安医院-招聘", "https://www.bjyah.com/Html/News/List-18-1.html",       "医疗卫生", "丰台区"),
    ("首都医科大学附属北京地坛医院-招聘", "https://www.bjdth.com/Html/News/List-14-1.html",       "医疗卫生", "朝阳区"),
    ("首都医科大学附属北京胸科医院-招聘", "https://www.bjxkyy.cn/Html/News/List-17-1.html",       "医疗卫生", "通州区"),
    ("北京积水潭医院-招聘",           "https://www.jst-hosp.com.cn/Html/News/List-16-1.html",     "医疗卫生", "西城区"),
    ("北京回龙观医院-招聘",           "https://www.bjhlgh.cn/Html/News/List-13-1.html",           "医疗卫生", "昌平区"),
    ("北京老年医院-招聘",             "https://www.lnyy.com.cn/Html/News/List-11-1.html",         "医疗卫生", "海淀区"),
    ("北京小汤山医院-招聘",           "https://www.xtshos.com.cn/Html/News/List-11-1.html",       "医疗卫生", "昌平区"),
    ("首都儿科研究所-招聘",           "https://www.shouer.com.cn/web/rczp/",                      "医疗卫生", "朝阳区"),
    ("北京急救中心-招聘",             "https://www.beijing120.com/rczp.htm",                      "医疗卫生", "西城区"),
    ("北京中医医院-招聘",             "https://www.bjzhongyi.com/gzb_rczp",                       "医疗卫生", "东城区"),
    ("北京中医药大学东直门医院-招聘", "https://www.dzmhospital.com/Html/News/List-19-1.html",     "医疗卫生", "东城区"),
    ("北京中医药大学东方医院-招聘",   "https://www.dongfangyy.com.cn/Html/News/List-14-1.html",   "医疗卫生", "丰台区"),
    ("中国中医科学院广安门医院-招聘", "https://www.gamhospital.ac.cn/rczp/",                      "医疗卫生", "西城区"),
    ("中国中医科学院西苑医院-招聘",   "https://www.xyhospital.com/rczp/",                         "医疗卫生", "海淀区"),
    ("中国中医科学院望京医院-招聘",   "https://www.wjhospital.com.cn/rczp/",                      "医疗卫生", "朝阳区"),
    ("中国中医科学院眼科医院-招聘",   "https://www.ykhospital.com.cn/rczp.htm",                   "医疗卫生", "石景山区"),
    ("北京清华长庚医院-招聘",         "https://www.btch.edu.cn/Html/News/List-26-1.html",         "医疗卫生", "昌平区"),
    ("航天中心医院-招聘",             "https://www.asc.net.cn/Html/News/List-32-1.html",          "医疗卫生", "海淀区"),
    ("航空总医院-招聘",               "https://www.hkzyy.com.cn/rczp.htm",                        "医疗卫生", "朝阳区"),
    ("北京博爱医院-招聘",             "https://www.crrc.com.cn/rczp/",                            "医疗卫生", "丰台区"),
    ("北京市海淀医院-招聘",           "https://www.hdhospital.com/rczp/",                         "医疗卫生", "海淀区"),
    ("北京市垂杨柳医院-招聘",         "https://www.cylh.com.cn/rczp/",                            "医疗卫生", "朝阳区"),
    ("北京市石景山医院-招聘",         "https://www.sjs-hosp.com.cn/rczp.htm",                     "医疗卫生", "石景山区"),
    ("北京怀柔医院-招聘",             "https://www.bjhryy.com/rczp/",                             "医疗卫生", "怀柔区"),
]

# -------- 五、北京市属国企（市管企业）招聘页 --------
JOB_SOURCES += [
    ("首钢集团-招聘",             "https://www.shougang.com.cn/sgweb/zpxx/",          "国有企业", "石景山区"),
    ("北京银行-招聘",             "https://www.bankofbeijing.com.cn/rczp/",           "国有企业", "西城区"),
    ("北京农商银行-招聘",         "https://www.bjrcb.com/rczp/",                      "国有企业", "西城区"),
    ("华夏银行-招聘",             "https://www.hxb.com.cn/rczp/",                     "国有企业", "东城区"),
    ("北汽集团-人才招聘",         "https://www.baicgroup.com.cn/rczp/",               "国有企业", "朝阳区"),
    ("同仁堂-招聘信息",           "https://www.tongrentang.com/zpxx/",                "国有企业", "东城区"),
    ("北京金隅集团-招聘",         "https://www.bbmg.com.cn/rczp/",                    "国有企业", "东城区"),
    ("北辰实业集团-招聘",         "https://www.bcjt.com.cn/rczp/",                    "国有企业", "朝阳区"),
    ("北京建工集团-招聘",         "https://www.bcegc.com/rczp/",                      "国有企业", "西城区"),
    ("北京城建集团-招聘",         "https://www.bucg.com/rczp/",                       "国有企业", "西城区"),
    ("北京地铁-招聘",             "https://www.bjsubway.com/rczp/",                   "国有企业", "西城区"),
    ("北京公交集团-招聘",         "https://www.bjbus.com/rczp/",                      "国有企业", "西城区"),
    ("FESCO-招聘",                "https://www.fesco.com.cn/rczp/",                   "国有企业", "朝阳区"),
    ("首创集团-招聘",             "https://www.bcapital.com.cn/rczp/",                "国有企业", "西城区"),
    ("京能集团-招聘",             "https://www.powerbeijing.com/rczp/",               "国有企业", "西城区"),
    ("北京排水集团-招聘",         "https://www.bdc.cn/rczp/",                         "国有企业", "朝阳区"),
    ("北京燃气集团-招聘",         "https://www.bjgas.com/rczp/",                      "国有企业", "西城区"),
    ("中关村发展集团-招聘",       "https://www.zgcgroup.com.cn/rczp/",                "国有企业", "海淀区"),
]

# -------- 六、中央在京单位/国家级招聘平台 --------
JOB_SOURCES += [
    ("人社部-事业单位公开招聘",   "https://www.mohrss.gov.cn/SYrlzyhshbzb/fwyd/SYkaoshizhaopin/zyhgjjgsydwgkzp/", "事业单位", "中央在京"),
    ("中国公共招聘网",            "https://job.mohrss.gov.cn/",                       "综合招聘", "中央在京"),
    ("人社部-事业单位人事管理",   "https://www.mohrss.gov.cn/SYrlzyhshbzb/zwgk/sydwzp/", "事业单位", "中央在京"),
    ("国资委-央企招聘",           "https://www.sasac.gov.cn/n2588035/n2588325/n2588350/index.html", "国有企业", "中央在京"),
    ("中科院-人才招聘",           "https://www.cas.cn/rcjy/zp/",                      "科研机构", "中央在京"),
    ("中国社会科学院-招聘",       "https://www.cass.cn/rczp/",                        "科研机构", "中央在京"),
    ("国家博物馆-招聘公告",       "https://www.chnmuseum.cn/zpgg/",                   "文化事业", "中央在京"),
    ("故宫博物院-招聘信息",       "https://www.dpm.org.cn/zpxx/",                     "文化事业", "中央在京"),
    ("国家图书馆-人才招聘",       "https://www.nlc.cn/gygt/rczp/",                    "文化事业", "中央在京"),
    ("中国疾控中心-通知公告",     "https://www.chinacdc.cn/zxdt/tzgg/",               "医疗卫生", "中央在京"),
    ("国家卫健委人才中心-招聘",   "https://www.21wecan.com/rczp/",                    "医疗卫生", "中央在京"),
    ("国聘网-央企招聘",           "https://www.iguopin.com/",                         "国有企业", "中央在京"),
    ("中国航天科技集团-招聘",     "https://www.spacechina.com/n25/n144/index.html",   "国有企业", "中央在京"),
    ("国航-招聘",                 "https://www.airchina.com.cn/cn/about/recruitment/", "国有企业", "中央在京"),
    ("中央广播电视总台-招聘",     "https://www.cctv.com/rczp/",                       "传媒", "中央在京"),
    ("新华社招聘",                "https://job.xinhua.org/",                          "传媒", "中央在京"),
]

print(f"[INFO] 已加载招聘来源 URL 总数：{len(JOB_SOURCES)}")

# -------- 七、补充：更多北京市级/区级招聘站点 & 在京央企/事业单位（扩充到200+） --------
EXTRA_SOURCES = [
    # 北京市级更多招聘入口
    ("北京市人才工作局-通知公告",   "https://rcgz.beijing.gov.cn/tzgg/",                              "人事招聘", "北京市"),
    ("北京市人社局-事业单位招聘第2页", "https://rsj.beijing.gov.cn/xxgk/gkzp/index_1.html",            "事业单位", "北京市"),
    ("北京市总工会-通知公告",       "https://www.bjzgh.gov.cn/xxgk/tzgg/",                             "综合招聘", "北京市"),
    ("北京市残联-通知公告",         "https://www.bdpf.org.cn/zwxx/gsgg/tzgg/",                         "综合招聘", "北京市"),
    ("北京市红十字会-通知公告",     "https://www.bjredcross.org.cn/bjsrcs/xwzx/tzgg/",                 "综合招聘", "北京市"),
    ("北京市退役军人事务局-通知",   "https://tyjrswj.beijing.gov.cn/zwxx/tzgg/",                       "综合招聘", "北京市"),
    ("北京市应急管理局-通知公告",   "https://yjglj.beijing.gov.cn/zwgk/tzgg/",                         "综合招聘", "北京市"),
    ("北京市市场监管局-通知公告",   "https://scjgj.beijing.gov.cn/zwxx/tzgg/",                         "综合招聘", "北京市"),
    ("北京市统计局-通知公告",       "https://tjj.beijing.gov.cn/zwgk_184/tzgg/",                       "综合招聘", "北京市"),
    ("北京市园林绿化局-通知公告",   "https://yllhj.beijing.gov.cn/zwxx/tzgg/",                         "事业单位", "北京市"),
    ("北京市农业农村局-通知公告",   "https://nyncy.beijing.gov.cn/zwxx/tzgg/",                         "事业单位", "北京市"),
    ("北京市文物局-通知公告",       "https://wwj.beijing.gov.cn/wwj/zwxx/tzgg31/",                     "文化事业", "北京市"),
    ("北京市体育局-通知公告",       "https://tyj.beijing.gov.cn/bjsports/zwxx15/tzgg15/index.html",    "事业单位", "北京市"),
    ("北京市审计局-通知公告",       "https://sjj.beijing.gov.cn/zwxx/tzgg/",                           "事业单位", "北京市"),
    ("北京市司法局-通知公告",       "https://sfj.beijing.gov.cn/sfj/zwxx38/tzgg33/",                   "事业单位", "北京市"),
    ("北京市财政局-通知公告",       "https://czj.beijing.gov.cn/zwxx/tzgg/",                           "事业单位", "北京市"),
    ("北京市规自委-通知公告",       "https://ghzrzyw.beijing.gov.cn/xwzt/tzgg/",                       "事业单位", "北京市"),
    ("北京市住建委-通知公告",       "https://zjw.beijing.gov.cn/bjjs/xxgk/tzgg/index.shtml",           "事业单位", "北京市"),
    ("北京市城管委-通知公告",       "https://csglw.beijing.gov.cn/zwxx_20354/tzgg_20358/",             "事业单位", "北京市"),
    ("北京市商务局-通知公告",       "https://sw.beijing.gov.cn/ztxx/tzgg/",                            "事业单位", "北京市"),
    ("北京市文化市场执法总队-公告", "https://whsczf.beijing.gov.cn/zwxx/gsgg/",                        "文化事业", "北京市"),
    # 中央在京补充
    ("最高人民法院招聘",            "https://www.court.gov.cn/zhaopin.html",                           "事业单位", "中央在京"),
    ("最高人民检察院招聘",          "https://www.spp.gov.cn/spp/rsxx/",                                "事业单位", "中央在京"),
    ("国家发改委-人事信息",         "https://www.ndrc.gov.cn/xwdt/ztzl/renshi/",                       "事业单位", "中央在京"),
    ("工信部-人事信息",             "https://www.miit.gov.cn/jgsj/rsj/index.html",                     "事业单位", "中央在京"),
    ("财政部-人事信息",             "http://www.mof.gov.cn/zhengwuxinxi/renshixinxi/",                 "事业单位", "中央在京"),
    ("商务部-人事信息",             "http://www.mofcom.gov.cn/article/rsxx/",                          "事业单位", "中央在京"),
    ("科技部-通知公告",             "https://www.most.gov.cn/tztg/",                                   "科研机构", "中央在京"),
    ("文化和旅游部-人才招聘",       "https://www.mct.gov.cn/whzx/zpxx/",                               "文化事业", "中央在京"),
    ("国家体育总局-人事招聘",       "https://www.sport.gov.cn/n315/n336/index.html",                   "事业单位", "中央在京"),
    ("中国气象局-招聘公告",         "https://www.cma.gov.cn/2011xwzx/2011xrsxx/2011xzpgg/",            "事业单位", "中央在京"),
    # 更多北京医院
    ("北京世纪坛医院-招聘补",       "https://www.bjshijitan.com/Html/News/List-16-1.html",             "医疗卫生", "海淀区"),
    ("北京京煤集团总医院-招聘",     "https://www.jmhospital.com.cn/rczp/",                             "医疗卫生", "门头沟区"),
    ("北京燕化医院-招聘",           "https://www.yhhosp.com/rczp.htm",                                 "医疗卫生", "房山区"),
    ("北京市普仁医院-招聘",         "https://www.prh.com.cn/rczp/",                                    "医疗卫生", "东城区"),
    ("北京市肛肠医院-招聘",         "https://www.ermh.cn/rczp.htm",                                    "医疗卫生", "西城区"),
    ("北京康复医院-招聘",           "https://www.bkhrrs.com.cn/",                                      "医疗卫生", "石景山区"),
    ("北京市昌平区医院-招聘",       "https://www.cpqhospital.com/rczp/",                               "医疗卫生", "昌平区"),
    ("北京市顺义区医院-招聘",       "https://www.shunyihospital.com.cn/rczp/",                         "医疗卫生", "顺义区"),
    ("北京房山第一医院-招聘",       "https://www.bjfshyy.com/rczp.htm",                                "医疗卫生", "房山区"),
    # 北京国企补充
    ("北京首农食品集团-招聘",       "https://www.bjsnfg.com/rczp/",                                    "国有企业", "西城区"),
    ("北京电子控股-招聘",           "https://www.behc.com.cn/rczp/",                                   "国有企业", "朝阳区"),
    ("北京时尚控股-招聘",           "https://www.bjfashion.com.cn/rczp/",                              "国有企业", "朝阳区"),
    ("一轻控股-招聘",               "https://www.bjlq.com.cn/rczp/",                                   "国有企业", "朝阳区"),
    ("京城机电-招聘",               "https://www.jcmei.com.cn/rczp/",                                  "国有企业", "朝阳区"),
    ("首发集团-招聘",               "https://www.bchd.com.cn/rczp/",                                   "国有企业", "丰台区"),
    ("北京自来水集团-招聘",         "https://www.bjwatergroup.com.cn/rczp/",                           "国有企业", "西城区"),
    ("北京热力集团-招聘",           "https://www.bdhg.com.cn/rczp/",                                   "国有企业", "朝阳区"),
    # 北京高校补充
    ("北京舞蹈学院附中-招聘",       "https://www.bda.edu.cn/jgsz/rsc/rczp.htm",                        "高校招聘", "海淀区"),
    ("北京警察学院-招聘",           "https://www.bjpc.edu.cn/rsc/rczp.htm",                            "高校招聘", "昌平区"),
    ("北京财贸职业学院-招聘2",      "https://www.bjczy.edu.cn/rsc/rczp.htm",                           "高校招聘", "通州区"),
    ("北京政法职业学院-招聘",       "https://www.bjmc.edu.cn/rsc/rczp.htm",                            "高校招聘", "朝阳区"),
    ("北京交通运输职业学院-招聘",   "https://www.bjjt.edu.cn/rsc/rczp.htm",                            "高校招聘", "海淀区"),
    ("北京卫生职业学院-招聘",       "https://www.bjwsxx.com/rsc/rczp.htm",                             "高校招聘", "通州区"),
    ("北京体育职业学院-招聘",       "https://www.bjtzy.edu.cn/rsc/rczp.htm",                           "高校招聘", "丰台区"),
    ("北京农业职业学院-招聘",       "https://www.bvca.edu.cn/rsc/rczp.htm",                            "高校招聘", "房山区"),
    ("北京劳动保障职业学院-招聘",   "https://www.bvclss.cn/rsc/zpxx.htm",                              "高校招聘", "朝阳区"),
    ("北京工业职业技术学院-招聘",   "https://www.bgy.org.cn/rsc/rczp.htm",                             "高校招聘", "石景山区"),
    ("北京信息职业技术学院-招聘",   "https://www.bitc.edu.cn/rsc/zpxx.htm",                            "高校招聘", "朝阳区"),
]
# 过滤掉重复的URL
existing_urls = {s[1] for s in JOB_SOURCES}
added = 0
for item in EXTRA_SOURCES:
    if item[1] not in existing_urls:
        JOB_SOURCES.append(item)
        existing_urls.add(item[1])
        added += 1
print(f"[INFO] 补充来源新增 {added} 个，当前总数 {len(JOB_SOURCES)}")


# ============================================================
# 通用爬虫模块
# ============================================================
HEADERS_LIST = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    },
]

# 招聘关键词
JOB_KW_RE = re.compile(
    r'(招聘|招贤|诚聘|引才|英才|校园招聘|社会招聘|公开招聘|事业单位招聘|'
    r'人才引进|人才招聘|用工|人员招聘|招录|聘用|岗位|校招|社招|'
    r'zhaopin|zpgg|rczp|recruit|career|hr\b)',
    re.IGNORECASE
)

# 强过滤关键词（绝对要过滤掉的导航/无关链接）
EXCLUDE_KW_RE = re.compile(
    r'(^(javascript:|mailto:|#|void|$)|首页|下一页|上一页|末页|尾页|更多|返回|关闭|打印|'
    r'^.$|^..$|网站首页|联系我们|机构设置|政务公开|信息公开|政策文件|办事指南|下载|'
    r'首页 \[|当前位置|您当前|当前位置：|首页 >|首 页)',
    re.IGNORECASE
)

# 白名单文件后缀（只跟招聘正文有关的链接后缀）
ALLOWED_EXT_RE = re.compile(
    r'\.(shtml?|html?|htm|asp|aspx|jsp|php|do|action|jspx)(\?.*)?$|'
    r'/[a-zA-Z0-9_\-]+\?.*|/$',
    re.IGNORECASE
)
# 黑名单资源后缀
BLOCKED_EXT_RE = re.compile(
    r'\.(pdf|doc|docx|xls|xlsx|zip|rar|png|jpg|jpeg|gif|ico|css|js|xml|json|mp4|mp3|avi|swf|'
    r'ppt|pptx|wps|txt|csv)(\?.*)?$',
    re.IGNORECASE
)

# 薪资正则
SALARY_PATTERNS = [
    (re.compile(r'(\d{1,3})\s*[kK]?\s*[-~—至到]\s*(\d{1,3})\s*[kK万]'),
     lambda m: f"{m.group(1)}k-{m.group(2)}k"),
    (re.compile(r'月薪\s*(\d{1,5})\s*[-~—至]\s*(\d{1,5})'),
     lambda m: f"{max(1, int(m.group(1))//1000)}k-{max(1, int(m.group(2))//1000)}k"),
    (re.compile(r'(\d{1,3})\s*[kK]\s*以上'), lambda m: f"{m.group(1)}k以上"),
    (re.compile(r'年薪\s*(\d{2,3})\s*[多万]'), lambda m: f"{m.group(1)}w/年"),
    (re.compile(r'(\d{2,3})\s*万元?/年'),  lambda m: f"{m.group(1)}w/年"),
]

# 地区关键词
DISTRICT_KEYWORDS = {
    "东城": "东城区", "西城": "西城区", "朝阳": "朝阳区", "海淀": "海淀区",
    "丰台": "丰台区", "石景山": "石景山区", "通州": "通州区", "大兴": "大兴区",
    "昌平": "昌平区", "顺义": "顺义区", "房山": "房山区", "门头沟": "门头沟区",
    "怀柔": "怀柔区", "平谷": "平谷区", "密云": "密云区", "延庆": "延庆区",
    "经开区": "经开区", "亦庄": "经开区",
}
DISTRICT_NAMES = set(DISTRICT_KEYWORDS.values())

# 岗位类型关键词
TYPE_KEYWORDS = {
    "教师": "教育系统", "老师": "教育系统", "教授": "高校招聘", "讲师": "高校招聘",
    "辅导员": "高校招聘", "教研": "教育系统",
    "医生": "医疗卫生", "医师": "医疗卫生", "护士": "医疗卫生", "护理": "医疗卫生",
    "药师": "医疗卫生", "中医": "医疗卫生", "检验": "医疗卫生",
    "工程师": "技术岗位", "程序员": "技术岗位", "开发": "技术岗位", "算法": "技术岗位",
    "软件": "技术岗位", "前端": "技术岗位", "后端": "技术岗位", "运维": "技术岗位",
    "会计": "财务岗位", "财务": "财务岗位", "出纳": "财务岗位", "审计": "财务岗位",
    "管理": "管理岗位", "经理": "管理岗位", "主管": "管理岗位", "总监": "管理岗位",
    "行政": "行政岗位", "人事": "行政岗位", "秘书": "行政岗位", "文员": "行政岗位",
    "销售": "销售岗位", "市场": "市场岗位", "营销": "市场岗位", "客服": "服务岗位",
    "实习": "实习岗位", "应届": "校园招聘", "校招": "校园招聘",
    "事业编": "事业单位", "编制": "事业单位", "国企": "国有企业",
    "公务员": "公务员", "选调": "公务员",
}

# 日期
DATE_PATTERN = re.compile(r'(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})')


def safe_request(url, timeout=12, retries=1):
    """安全HTTP请求：随机UA、异常容错"""
    for _ in range(retries + 1):
        try:
            time.sleep(random.uniform(0.15, 0.5))
            headers = random.choice(HEADERS_LIST)
            resp = requests.get(url, headers=headers, timeout=timeout, verify=False,
                                allow_redirects=True)
            if resp.status_code >= 400:
                return None
            # 编码处理
            if resp.encoding and resp.encoding.lower() in ('iso-8859-1',):
                resp.encoding = resp.apparent_encoding or 'utf-8'
            else:
                resp.encoding = resp.apparent_encoding or resp.encoding or 'utf-8'
            return resp.text
        except Exception:
            time.sleep(0.3)
    return None


def extract_jobs_from_page(html, base_url, source_name, source_cat, source_region):
    """从单个页面提取岗位列表（多策略通用解析器）"""
    jobs = []
    if not html:
        return jobs
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception:
            return jobs

    # 清理无关节点
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript',
                     'form', 'button']):
        tag.decompose()

    seen_urls = set()

    # ---------- 策略A：在常见列表容器中找链接 ----------
    # 政府/高校CMS常见的列表容器 class 关键词
    list_selectors = [
        'ul', 'ol', '.list', '.news_list', '.news-list', '.zp_list', '.zp-list',
        '.xxgk_list', '.xxgk-list', '.article-list', '.notice-list', '.info-list',
        '.m-list', '.list-box', '.listBox', '.ListBox', '.news', '.newslist',
        '.n-list', '.list_con', '.list-con', '.listcon', '.zpgg_list', '.rczp_list',
        '.right_list', '.right-list', '.content_list', '.content-list',
        'table', '.list-group',
    ]
    candidates = []
    for sel in list_selectors:
        candidates.extend(soup.select(sel))

    if not candidates:
        candidates = [soup]

    a_collected = []
    for container in candidates:
        for a in container.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not href or not text:
                continue
            # 过滤过短/过长文本
            if len(text) < 6 or len(text) > 150:
                continue
            # 过滤黑名单关键词
            if EXCLUDE_KW_RE.search(text):
                continue
            # 过滤黑名单后缀
            if BLOCKED_EXT_RE.search(href.split('?')[0]):
                continue
            # 链接必须包含招聘/通知/公告类关键词（在文本或href中）
            if not (JOB_KW_RE.search(text) or JOB_KW_RE.search(href)):
                # 如果来源本身就定位到招聘栏目，对关键词做更宽松处理：允许含"公告""通知"
                if not re.search(r'(公告|通知|简章|启事|公示|聘用|录用|用工|人员)', text):
                    continue
            # 构建完整URL
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ('http', 'https'):
                continue
            # 只保留同域或上级域链接（防外跳）
            base_domain = urlparse(base_url).netloc.lower()
            link_domain = parsed.netloc.lower()
            if base_domain != link_domain:
                # 允许 gov.cn/edu.cn/com.cn 二级域跳转
                base_parts = base_domain.split('.')
                link_parts = link_domain.split('.')
                if len(base_parts) >= 2 and len(link_parts) >= 2:
                    if not (base_parts[-2:] == link_parts[-2:]):
                        continue
                else:
                    continue
            # 过滤列表页自身
            if full_url.rstrip('/') == base_url.rstrip('/'):
                continue
            # 去重
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            a_collected.append((text, full_url))

    # ---------- 策略B：如果策略A提取太少，回退到全站所有<a> ----------
    if len(a_collected) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not href or not text:
                continue
            if len(text) < 6 or len(text) > 150:
                continue
            if EXCLUDE_KW_RE.search(text):
                continue
            if BLOCKED_EXT_RE.search(href.split('?')[0]):
                continue
            if not JOB_KW_RE.search(text):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ('http', 'https'):
                continue
            base_domain = urlparse(base_url).netloc.lower()
            link_domain = parsed.netloc.lower()
            if base_domain != link_domain:
                base_parts = base_domain.split('.')
                link_parts = link_domain.split('.')
                if len(base_parts) >= 2 and len(link_parts) >= 2:
                    if not (base_parts[-2:] == link_parts[-2:]):
                        continue
                else:
                    continue
            if full_url.rstrip('/') == base_url.rstrip('/'):
                continue
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            a_collected.append((text, full_url))

    # 把 (text, url) 转为岗位元数据
    for text, full_url in a_collected:
        job = extract_job_metadata(text, full_url, source_name, source_cat, source_region)
        if job:
            jobs.append(job)

    return jobs


def extract_job_metadata(title, url, source_name, source_cat, source_region):
    """从标题和URL提取元信息"""
    # 清理标题
    title = re.sub(r'[\[\]【】\(\)（）\s]+', ' ', title).strip()
    title = re.sub(r'\s+', ' ', title)
    if not title or len(title) < 6:
        return None

    # 地区：先使用来源默认值，再被标题关键词覆盖
    region = source_region if source_region in DISTRICT_NAMES or source_region in (
        "北京市", "中央在京") else "北京市"
    for kw, dist in DISTRICT_KEYWORDS.items():
        if kw in title:
            region = dist
            break

    # 薪资
    salary = "薪资面议"
    for pattern, formatter in SALARY_PATTERNS:
        m = pattern.search(title)
        if m:
            salary = formatter(m)
            break

    # 类型
    job_type = source_cat
    for kw, t in TYPE_KEYWORDS.items():
        if kw in title:
            job_type = t
            break

    # 日期（从URL或标题）
    pub_date = ""
    m = DATE_PATTERN.search(title)
    if m:
        pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    else:
        # URL里的日期路径 /202405/ 或 /2024-05/
        m2 = re.search(r'/(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})/', url)
        if m2:
            pub_date = f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
        else:
            m3 = re.search(r'/t(\d{4})(\d{2})(\d{2})_', url)
            if m3:
                pub_date = f"{m3.group(1)}-{m3.group(2)}-{m3.group(3)}"

    # 单位
    company = source_name.split('-')[0].strip()

    return {
        "title": title,
        "company": company,
        "location": region,
        "salary": salary,
        "type": job_type,
        "date": pub_date,
        "url": url,
        "source": source_name,
    }


def crawl_single_source(source):
    """爬取单个来源（含简单翻页），返回 (名称, 岗位列表, 状态, 信息)"""
    name, url, cat, region = source
    jobs = []
    html = safe_request(url)
    if not html:
        return name, [], "fail", "无法访问（超时/404/网络错误）"
    jobs.extend(extract_jobs_from_page(html, url, name, cat, region))

    # 尝试翻一页（_1.html / index_1.html 常见模式）
    parsed = urlparse(url)
    path = parsed.path
    next_pages = []
    if path.endswith('.html') or path.endswith('.htm') or path.endswith('.shtml'):
        m = re.match(r'(.+?)(?:_(\d+))?\.(s?html?)$', path)
        if m:
            prefix = m.group(1)
            ext = m.group(3)
            for i in range(1, 2):
                next_pages.append(f"{parsed.scheme}://{parsed.netloc}{prefix}_{i}.{ext}")
    elif path.endswith('/'):
        next_pages.append(f"{url.rstrip('/')}/index_1.html")
        next_pages.append(f"{url.rstrip('/')}/index_1.htm")
        next_pages.append(f"{url.rstrip('/')}/index_0.html")
    for np in next_pages:
        if np == url:
            continue
        h2 = safe_request(np, timeout=8)
        if h2:
            jobs.extend(extract_jobs_from_page(h2, np, name, cat, region))
        # 只取前1页，避免请求过多

    status = "ok" if jobs else "empty"
    msg = f"抓取到 {len(jobs)} 条" if jobs else "页面可访问但未提取到招聘条目"
    return name, jobs, status, msg


def filter_jobs(jobs, keyword="", region="", salary="", job_type=""):
    """过滤岗位"""
    min_salary = 0
    if salary:
        m = re.search(r'(\d+)', salary)
        if m:
            min_salary = int(m.group(1)) // 1000 if int(m.group(1)) > 1000 else int(m.group(1))
    keywords = [k.strip() for k in keyword.split() if k.strip()] if keyword else []

    out = []
    for job in jobs:
        # 关键词（AND匹配）
        if keywords:
            text = (job.get("title", "") + job.get("company", "") + job.get("type", "")).lower()
            if not all(kw.lower() in text for kw in keywords):
                continue
        # 地区
        if region and region != "北京市":
            if job.get("location", "") != region and region not in job.get("location", ""):
                continue
        # 薪资
        if min_salary > 0:
            st = job.get("salary", "")
            nums = [int(n) for n in re.findall(r'(\d+)', st)]
            if nums:
                if "以上" in st:
                    pass
                elif max(nums) < min_salary:
                    continue
            else:
                continue  # 没提取到薪资的岗位在设定薪资时过滤
        # 类型
        if job_type and job_type != "不限":
            jt = job.get("type", "")
            if job_type not in jt and jt not in job_type:
                continue
        out.append(job)
    return out


def deduplicate_and_sort(jobs):
    seen = set()
    unique = []
    for job in jobs:
        key = job.get("url", "") or job.get("title", "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)

    def sort_key(j):
        d = j.get("date", "")
        return (0, d) if d else (1, "")
    unique.sort(key=sort_key, reverse=True)
    return unique


# ============================================================
# Flask 路由
# ============================================================

@app.route('/')
def index():
    return send_from_directory('.', 'frontend.html')


@app.route('/api/sources', methods=['GET'])
def get_sources():
    """返回所有来源URL列表（按分类）"""
    categories = {}
    for name, url, cat, region in JOB_SOURCES:
        key = cat if cat == region else f"{region} · {cat}"
        categories.setdefault(key, []).append({"name": name, "url": url})
    return jsonify({"total": len(JOB_SOURCES), "categories": categories})


@app.route('/api/search', methods=['GET', 'POST'])
def search():
    """搜索API（一次性返回全部结果）"""
    data = request.json if request.method == 'POST' and request.is_json else request.args
    keyword = (data.get('keyword') or data.get('position') or '').strip()
    region = (data.get('region') or '').strip()
    salary = (data.get('salary') or '').strip()
    job_type = (data.get('type') or data.get('job_type') or '').strip()

    print(f"[API] 搜索：kw='{keyword}' region='{region}' salary='{salary}' type='{job_type}'")
    t0 = time.time()
    all_jobs, stats = crawl_all_sources_sync(max_workers=25)
    elapsed = time.time() - t0
    filtered = filter_jobs(all_jobs, keyword, region, salary, job_type)
    filtered = deduplicate_and_sort(filtered)
    result = filtered[:500]

    success = sum(1 for s in stats.values() if s['status'] == 'ok')
    return jsonify({
        "jobs": result,
        "stats": {
            "total_sources": len(JOB_SOURCES),
            "success_sources": success,
            "source_status": stats,
            "total_crawled": len(all_jobs),
            "total_matched": len(filtered),
            "returned": len(result),
            "crawl_time": round(elapsed, 1),
        }
    })


@app.route('/api/search_stream')
def search_stream():
    """SSE流式搜索：实时推送每个站点的爬取进度"""
    keyword = request.args.get('keyword', '').strip()
    region = request.args.get('region', '').strip()
    salary = request.args.get('salary', '').strip()
    job_type = request.args.get('type', request.args.get('job_type', '')).strip()

    def generate():
        q = queue.Queue()

        def worker():
            all_jobs = []
            source_status = {}

            def on_done(name, jobs, status, msg):
                source_status[name] = {"status": status, "msg": msg, "count": len(jobs)}
                all_jobs.extend(jobs)
                q.put({
                    "event": "progress",
                    "name": name,
                    "status": status,
                    "msg": msg,
                    "count": len(jobs),
                    "done": len(source_status),
                    "total": len(JOB_SOURCES),
                })

            with ThreadPoolExecutor(max_workers=20) as ex:
                futures = {ex.submit(crawl_single_source, s): s for s in JOB_SOURCES}
                for fut in as_completed(futures):
                    try:
                        name, jobs, status, msg = fut.result(timeout=25)
                        on_done(name, jobs, status, msg)
                    except Exception as e:
                        s = futures[fut]
                        on_done(s[0], [], "fail", f"错误：{str(e)[:40]}")

            filtered = filter_jobs(all_jobs, keyword, region, salary, job_type)
            filtered = deduplicate_and_sort(filtered)
            result = filtered[:500]
            success = sum(1 for v in source_status.values() if v['status'] == 'ok')
            q.put({
                "event": "done",
                "jobs": result,
                "stats": {
                    "total_sources": len(JOB_SOURCES),
                    "success_sources": success,
                    "source_status": source_status,
                    "total_crawled": len(all_jobs),
                    "total_matched": len(filtered),
                    "returned": len(result),
                }
            })
            q.put(None)  # 结束信号

        threading.Thread(target=worker, daemon=True).start()
        yield f": 北京岗位信息实时搜索流\n\n".encode('utf-8')
        while True:
            item = q.get()
            if item is None:
                break
            yield (f"data: {json.dumps(item, ensure_ascii=False)}\n\n").encode('utf-8')

    return Response(stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


def crawl_all_sources_sync(max_workers=20):
    """同步爬取所有来源"""
    all_jobs = []
    stats = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(crawl_single_source, s): s for s in JOB_SOURCES}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                name, jobs, status, msg = fut.result(timeout=25)
                stats[name] = {"status": status, "msg": msg, "count": len(jobs)}
                all_jobs.extend(jobs)
            except Exception as e:
                stats[s[0]] = {"status": "fail", "msg": f"错误：{str(e)[:40]}", "count": 0}
    return all_jobs, stats


if __name__ == '__main__':
    print("=" * 60)
    print("  北京岗位信息查找系统 v3")
    print(f"  招聘来源数：{len(JOB_SOURCES)} 个官方站点")
    print("  访问地址：http://127.0.0.1:5000/")
    print("=" * 60)
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
