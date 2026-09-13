# -*- coding: utf-8 -*-
"""
北京岗位信息查找系统 - 后端服务（扩展版：200+ 官方来源）
运行方式: python backend.py
访问地址: http://127.0.0.1:5000/
"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
import threading

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# ============================================================
# 200+ 真实官方招聘来源 URL 列表
# 分类：市级平台 / 16区政府 / 北京高校 / 北京医院 / 市属国企 / 中央在京单位
# ============================================================

JOB_SOURCES = []

# ---------- 一、市级综合招聘平台（约 15 个） ----------
CITY_PLATFORMS = [
    ("北京市人社局-事业单位公开招聘", "https://rsj.beijing.gov.cn/xxgk/gkzp/", "事业单位", "市级综合"),
    ("北京市人社局-首页通知公告", "https://rsj.beijing.gov.cn/", "综合", "市级综合"),
    ("首都之窗-事业单位招聘", "https://www.beijing.gov.cn/gongkai/rsxx/sydwzp/", "事业单位", "市级综合"),
    ("北京高校大学生就业创业信息网", "https://www.bjbys.net.cn/zp/sydwzp/", "事业单位", "市级综合"),
    ("北京高校就业网-名企信息", "https://m.bjbys.net.cn/zp/mqxx/", "企业招聘", "市级综合"),
    ("北京高校就业网-就业双选会", "https://jobs.bjbys.net.cn/", "校园招聘", "市级综合"),
    ("北京市国资委-国企招聘", "https://gzw.beijing.gov.cn/yggq/gqzp/", "国有企业", "市级综合"),
    ("北京市国资委-国企招聘第2页", "https://gzw.beijing.gov.cn/yggq/gqzp/index_1.html", "国有企业", "市级综合"),
    ("北京市国资委-企业人才培养", "https://gzw.beijing.gov.cn/yggq/qyrcpx/", "国有企业", "市级综合"),
    ("北京市教委-通知公告", "https://jw.beijing.gov.cn/zwgk_20199/tzgg/", "教育系统", "市级综合"),
    ("北京市卫健委-通知公告", "https://wjw.beijing.gov.cn/zwgk_20040/tzgg/", "医疗卫生", "市级综合"),
    ("东城区-企业招聘", "https://www.bjdch.gov.cn/zwgk/zdlygk/wgjyzdly/qyzp/", "国有企业", "东城区"),
    ("密云区-就业信息", "https://www.bjmy.gov.cn/zwgk/zfxxgk/fdzdgknr/zdmsxx/jiuye/", "综合招聘", "密云区"),
    ("北京人事考试网", "https://rsj.beijing.gov.cn/ywsite/bjpta/", "考试招聘", "市级综合"),
    ("北京市就业超市", "https://fuwu.rsj.beijing.gov.cn/zhrs/jycy/jyzbjpc/home", "综合招聘", "市级综合"),
]
JOB_SOURCES.extend(CITY_PLATFORMS)

# ---------- 二、北京市 16 区 + 经开区政府网站招聘栏目（约 50 个） ----------
DISTRICTS = [
    # 域名格式：www.bjxxx.gov.cn，招聘栏目路径稍有不同，覆盖常见路径
    ("dongcheng", "东城区"), ("xicheng", "西城区"), ("chaoyang", "朝阳区"),
    ("haidian", "海淀区"), ("fengtai", "丰台区"), ("shijingshan", "石景山区"),
    ("tongzhou", "通州区"), ("daxing", "大兴区"), ("changping", "昌平区"),
    ("shunyi", "顺义区"), ("fangshan", "房山区"), ("mentougou", "门头沟区"),
    ("huairou", "怀柔区"), ("pinggu", "平谷区"), ("miyun", "密云区"),
    ("yanqing", "延庆区"),
]

# 各区招聘相关常见路径模式
DISTRICT_PATHS = [
    ("/zwgk/zdlygk/rlzy/kszp/", "考试招聘"),
    ("/zwgk/zdlygk/wgjyzdly/qyzp/", "企业招聘"),
    ("/zwgk/tzgg/", "通知公告"),
    ("/xxgk/gkzp/", "公开招聘"),
    ("/rsxx/sydwzp/", "事业单位招聘"),
]

for district_en, district_cn in DISTRICTS:
    domain = f"https://www.bj{district_en}.gov.cn"
    for i, (path, cat) in enumerate(DISTRICT_PATHS):
        JOB_SOURCES.append((f"{district_cn}政府-{cat}", domain + path, cat, district_cn))

# 经开区
JOB_SOURCES.append(("北京经开区-招聘公告", "https://www.bda.gov.cn/zwgk/tzgg/", "综合招聘", "经开区"))
JOB_SOURCES.append(("北京经开区-人事信息", "https://www.bda.gov.cn/gongkai/rsxx/", "人事招聘", "经开区"))

# ---------- 三、北京主要高校官方人才招聘页（约 80 个） ----------
UNIVERSITIES = [
    # (学校名称, 招聘页面URL或域名基础)
    ("北京大学", "https://hr.pku.edu.cn/rczp/"),
    ("北京大学-劳动合同制招聘", "https://hr.pku.edu.cn/rczp/xzjf/ldhtzp/"),
    ("北京大学招聘系统", "https://rszp.pku.edu.cn/recruit"),
    ("清华大学", "https://hr.tsinghua.edu.cn/zpxx.htm"),
    ("清华大学人才招聘", "https://jobs.tsinghua.edu.cn/"),
    ("中国人民大学", "https://hr.ruc.edu.cn/zpxx/rczp/"),
    ("北京师范大学", "https://rsc.bnu.edu.cn/docs/rczp.htm"),
    ("北京航空航天大学", "https://rsc.buaa.edu.cn/rczp.htm"),
    ("北京理工大学", "https://renshichu.bit.edu.cn/rczp/"),
    ("北京邮电大学", "https://hr.bupt.edu.cn/rczp.htm"),
    ("北京交通大学", "https://rsc.bjtu.edu.cn/tzgg/rczp.htm"),
    ("北京科技大学", "https://rsc.ustb.edu.cn/zpgg/"),
    ("北京化工大学", "https://rsc.buct.edu.cn/zpxx/"),
    ("北京林业大学", "https://rsc.bjfu.edu.cn/rczp/"),
    ("中国农业大学", "https://rcb.cau.edu.cn/col/col41894/index.html"),
    ("北京中医药大学", "https://rsc.bucm.edu.cn/rczp/"),
    ("北京协和医学院", "https://www.pumc.edu.cn/renshichu/zpxx.htm"),
    ("首都医科大学", "https://rsc.ccmu.edu.cn/rczp.htm"),
    ("北京外国语大学", "https://rsc.bfsu.edu.cn/rczp/"),
    ("北京语言大学", "https://rsc.blcu.edu.cn/col/col11452/index.html"),
    ("对外经济贸易大学", "https://rsc.uibe.edu.cn/rczp.htm"),
    ("中央财经大学", "https://rsc.cufe.edu.cn/zpxx/rczp.htm"),
    ("中国政法大学", "https://hr.cupl.edu.cn/rczp.htm"),
    ("中国传媒大学", "https://renshichu.cuc.edu.cn/rczp/"),
    ("中央民族大学", "https://hr.muc.edu.cn/rczp.htm"),
    ("中国矿业大学(北京)", "https://renshichu.cumtb.edu.cn/zpxx.htm"),
    ("中国石油大学(北京)", "https://www.cup.edu.cn/rsc/rczp/"),
    ("中国地质大学(北京)", "https://rsc.cugb.edu.cn/rczp.htm"),
    ("北京工业大学", "https://zhaopin.bjut.edu.cn/zpgg.htm"),
    ("首都师范大学", "https://rsc.cnu.edu.cn/rczp/"),
    ("首都经济贸易大学", "https://rsc.cueb.edu.cn/rczp.htm"),
    ("北京工商大学", "https://rsc.btbu.edu.cn/rczp.htm"),
    ("北京建筑大学", "https://rsc.bucea.edu.cn/rczp.htm"),
    ("北京信息科技大学", "https://rsc.bistu.edu.cn/rczp/"),
    ("北方工业大学", "https://rsc.ncut.edu.cn/zpxx.htm"),
    ("北京第二外国语学院", "https://rsc.bisu.edu.cn/rczp.htm"),
    ("北京服装学院", "https://rsc.bift.edu.cn/zpxx/"),
    ("北京印刷学院", "https://rsc.bigc.edu.cn/rczp.htm"),
    ("北京石油化工学院", "https://rsc.bipt.edu.cn/rczp.htm"),
    ("北京农学院", "https://rsc.bua.edu.cn/rczp/"),
    ("北京物资学院", "https://rsc.bwu.edu.cn/rczp.htm"),
    ("北京舞蹈学院", "https://www.bda.edu.cn/jgsz/rsc/rczp.htm"),
    ("北京电影学院", "https://www.bfa.edu.cn/rsc/rczp.htm"),
    ("中央戏剧学院", "https://web.zhongxi.cn/xyrczp/"),
    ("中央美术学院", "https://www.cafa.edu.cn/jgsz/renshichu/zpxx/"),
    ("中央音乐学院", "https://www.ccom.edu.cn/rsc/rczp/"),
    ("中国音乐学院", "https://www.ccmusic.edu.cn/rsc/rczp.htm"),
    ("北京体育大学", "https://zs.bsu.edu.cn/rczp/"),
    ("中国人民公安大学", "https://www.ppsuc.edu.cn/jgsz/renshichu/zpxx.htm"),
    ("中国青年政治学院", "https://www.cyu.edu.cn/rsc/rczp/"),
    ("北京联合大学", "https://rsc.buu.edu.cn/rczp/"),
    ("北京城市学院", "https://www.bcu.edu.cn/hr/zpxx.htm"),
    ("北京吉利学院", "https://www.bgu.edu.cn/rczp.htm"),
    ("北京电子科技学院", "https://www.besti.edu.cn/rsc/zpxx/"),
    ("外交学院", "https://www.cfau.edu.cn/renshichu/zpxx.htm"),
    ("国际关系学院", "https://www.uir.cn/rsc/rczp.htm"),
    ("中华女子学院", "https://www.cwu.edu.cn/rsc/rczp/"),
    ("中国劳动关系学院", "https://www.ciir.edu.cn/rsc/rczp.htm"),
    ("北京青年政治学院", "https://www.bjypc.edu.cn/rsc/zpxx.htm"),
    ("北京警察学院", "https://www.bjpc.edu.cn/rsc/rczp.htm"),
    ("北京财贸职业学院", "https://www.bjczy.edu.cn/rsc/rczp.htm"),
    ("北京电子科技职业学院", "https://www.dky.edu.cn/rsc/zpxx.htm"),
    ("北京工业职业技术学院", "https://www.bgy.org.cn/rsc/rczp.htm"),
    ("北京信息职业技术学院", "https://www.bitc.edu.cn/rsc/zpxx.htm"),
    ("北京农业职业学院", "https://www.bvca.edu.cn/rsc/rczp.htm"),
    ("北京劳动保障职业学院", "https://www.bvclss.cn/rsc/zpxx.htm"),
    ("北京社会管理职业学院", "https://www.bcsa.edu.cn/rsc/rczp.htm"),
    ("中国科学院大学", "https://rc.ucas.ac.cn/index.php/zh/zhaopin"),
    ("中国社会科学院大学", "https://www.ucass.edu.cn/rsc/rczp.htm"),
    ("北京师范大学-珠海校区(京招)", "https://rsc.bnu.edu.cn/docs/rczp.htm"),
    ("北京邮电大学世纪学院", "https://www.ccbupt.cn/rsc/rczp.htm"),
    ("北京工商大学嘉华学院", "https://www.canvard.edu.cn/rsc/zpxx.htm"),
    ("北京工业大学耿丹学院", "https://www.gengdan.cn/rsc/rczp.htm"),
    ("北京第二外国语学院中瑞酒店管理学院", "https://www.bhi.edu.cn/rsc/zpxx.htm"),
    ("首都师范大学科德学院", "https://www.kdc.edu.cn/rsc/rczp.htm"),
    ("北京开放大学", "https://www.bjou.edu.cn/rsc/zpxx.htm"),
    ("北京教育学院", "https://www.bjie.ac.cn/rsc/rczp.htm"),
    ("北京教育科学研究院", "https://www.bjesr.cn/rsc/zpxx.htm"),
    ("北京市委党校", "https://www.bac.gov.cn/rsc/rczp.htm"),
]
for name, url in UNIVERSITIES:
    JOB_SOURCES.append((f"{name}-人才招聘", url, "高校招聘", "北京高校"))

# ---------- 四、北京主要医院/医疗机构招聘页（约 50 个） ----------
HOSPITALS = [
    ("北京协和医院", "https://www.pumch.cn/notice/hr.html"),
    ("北京医院", "https://www.bjhmoh.cn/"),
    ("中日友好医院", "https://www.zryhyy.com.cn/Html/News/List-14-1.html"),
    ("北京大学第一医院", "https://www.bddyyy.com.cn/rczp.htm"),
    ("北京大学人民医院", "https://www.pkuph.cn/rencai/zhaopin/"),
    ("北京大学第三医院", "https://www.puh3.net.cn/rczp/"),
    ("北京大学口腔医院", "https://ss.bjmu.edu.cn/rczp/"),
    ("北京大学肿瘤医院", "https://www.bjcancer.org/Html/News/List-23-1.html"),
    ("北京大学第六医院", "https://www.pkuh6.cn/Html/News/List-11-1.html"),
    ("首都医科大学附属北京友谊医院", "https://www.bfh.com.cn/Html/News/List-21-1.html"),
    ("首都医科大学附属北京同仁医院", "https://www.trhos.com/Html/News/List-28-1.html"),
    ("首都医科大学附属北京朝阳医院", "https://www.bjcyh.com.cn/Html/News/List-15-1.html"),
    ("首都医科大学附属北京天坛医院", "https://www.bjtth.org/Html/News/List-14-1.html"),
    ("首都医科大学附属北京安贞医院", "https://www.anzhen.org/Html/News/List-19-1.html"),
    ("首都医科大学附属北京世纪坛医院", "https://www.bjshijitan.com/Html/News/List-16-1.html"),
    ("首都医科大学宣武医院", "https://www.xwhosp.com.cn/Html/News/List-17-1.html"),
    ("首都医科大学附属北京儿童医院", "https://www.bch.com.cn/Html/News/List-20-1.html"),
    ("首都医科大学附属北京口腔医院", "https://www.dentist.org.cn/rczp.htm"),
    ("首都医科大学附属北京安定医院", "https://www.bjad.com.cn/Html/News/List-12-1.html"),
    ("首都医科大学附属北京佑安医院", "https://www.bjyah.com/Html/News/List-18-1.html"),
    ("首都医科大学附属北京地坛医院", "https://www.bjdth.com/Html/News/List-14-1.html"),
    ("首都医科大学附属北京胸科医院", "https://www.bjxkyy.cn/Html/News/List-17-1.html"),
    ("北京积水潭医院", "https://www.jst-hosp.com.cn/Html/News/List-16-1.html"),
    ("北京回龙观医院", "https://www.bjhlgh.cn/Html/News/List-13-1.html"),
    ("北京老年医院", "https://www.lnyy.com.cn/Html/News/List-11-1.html"),
    ("北京小汤山医院", "https://www.xtshos.com.cn/Html/News/List-11-1.html"),
    ("首都儿科研究所", "https://www.shouer.com.cn/web/rczp/"),
    ("北京急救中心", "https://www.beijing120.com/rczp.htm"),
    ("北京中医医院", "https://www.bjzhongyi.com/gzb_rczp"),
    ("北京中医药大学东直门医院", "https://www.dzmhospital.com/Html/News/List-19-1.html"),
    ("北京中医药大学东方医院", "https://www.dongfangyy.com.cn/Html/News/List-14-1.html"),
    ("中国中医科学院广安门医院", "https://www.gamhospital.ac.cn/rczp/"),
    ("中国中医科学院西苑医院", "https://www.xyhospital.com/rczp/"),
    ("中国中医科学院望京医院", "https://www.wjhospital.com.cn/rczp/"),
    ("中国中医科学院眼科医院", "https://www.ykhospital.com.cn/rczp.htm"),
    ("北京清华长庚医院", "https://www.btch.edu.cn/Html/News/List-26-1.html"),
    ("航天中心医院", "https://www.asc.net.cn/Html/News/List-32-1.html"),
    ("航空总医院", "https://www.hkzyy.com.cn/rczp.htm"),
    ("北京京煤集团总医院", "https://www.jmhospital.com.cn/rczp/"),
    ("北京燕化医院", "https://www.yhhosp.com/rczp.htm"),
    ("北京博爱医院", "https://www.crrc.com.cn/rczp/"),
    ("首都医科大学附属北京康复医院", "https://www.bkhrrs.com.cn/"),
    ("北京市肛肠医院", "https://www.ermh.cn/rczp.htm"),
    ("北京市普仁医院", "https://www.prh.com.cn/rczp/"),
    ("北京市海淀医院", "https://www.hdhospital.com/rczp/"),
    ("北京市垂杨柳医院", "https://www.cylh.com.cn/rczp/"),
    ("北京市石景山医院", "https://www.sjs-hosp.com.cn/rczp.htm"),
    ("北京市昌平区医院", "https://www.cpqhospital.com/rczp/"),
    ("北京怀柔医院", "https://www.bjhryy.com/rczp/"),
    ("北京密云区医院", "https://www.bjmyyy.com/rczp/"),
]
for name, url in HOSPITALS:
    JOB_SOURCES.append((f"{name}-招聘", url, "医疗卫生", "北京医院"))

# ---------- 五、北京市属国企（市管企业）官网招聘页（约 45 个） ----------
SOES = [
    ("首钢集团", "https://www.shougang.com.cn/sgweb/zpxx/"),
    ("北汽集团", "https://www.baicgroup.com.cn/rczp/"),
    ("北京同仁堂集团", "https://www.tongrentang.com/zpxx/"),
    ("北京金隅集团", "https://www.bbmg.com.cn/rczp/"),
    ("北京首都创业集团", "https://www.bcapital.com.cn/rczp/"),
    ("北京控股集团", "https://www.begcl.com/rczp/"),
    ("北京能源集团", "https://www.powerbeijing.com/rczp/"),
    ("北京市基础设施投资有限公司", "https://www.bii.com.cn/rczp/"),
    ("北京市地铁运营有限公司", "https://www.bjsubway.com/rczp/"),
    ("北京建工集团", "https://www.bcegc.com/rczp/"),
    ("北京城建集团", "https://www.bucg.com/rczp/"),
    ("北京首都开发控股集团", "https://www.bcdc.com.cn/rczp/"),
    ("北京住总集团", "https://www.bjzzh.com/rczp/"),
    ("北京市政路桥集团", "https://www.bmec.com.cn/rczp/"),
    ("北京北辰实业集团", "https://www.bcjt.com.cn/rczp/"),
    ("北京市首都公路发展集团", "https://www.bchd.com.cn/rczp/"),
    ("北京公共交通控股集团", "https://www.bjbus.com/rczp/"),
    ("北京祥龙资产经营", "https://www.bjxianglong.com/rczp/"),
    ("北京时尚控股", "https://www.bjfashion.com.cn/rczp/"),
    ("北京电子控股", "https://www.behc.com.cn/rczp/"),
    ("北京京城机电控股", "https://www.jcmei.com.cn/rczp/"),
    ("北京一轻控股", "https://www.bjlq.com.cn/rczp/"),
    ("北京二商集团", "https://www.bjesg.com.cn/rczp/"),
    ("北京粮食集团", "https://www.bjlsjt.com/rczp/"),
    ("北京王府井东安集团", "https://www.wfj.com.cn/rczp/"),
    ("北京对外经贸控股", "https://www.bjfth.com.cn/rczp/"),
    ("北京金融街集团", "https://www.bjfsg.com/rczp/"),
    ("北京首都旅游集团", "https://www.btg.com.cn/rczp/"),
    ("北京古玩城集团", "https://www.bjgwc.com/rczp/"),
    ("北京外企服务集团(FESCO)", "https://www.fesco.com.cn/rczp/"),
    ("北京国际信托", "https://www.bjitic.com/rczp/"),
    ("北京银行", "https://www.bankofbeijing.com.cn/rczp/"),
    ("北京农村商业银行", "https://www.bjrcb.com/rczp/"),
    ("华夏银行", "https://www.hxb.com.cn/rczp/"),
    ("北京证券公司", "https://www.bjzq.com.cn/rczp/"),
    ("中关村发展集团", "https://www.zgcgroup.com.cn/rczp/"),
    ("北京国有资本运营管理有限公司", "https://www.bjgzw.com/rczp/"),
    ("北京市国有资产经营公司", "https://www.bjgz.com.cn/rczp/"),
    ("京仪集团", "https://www.bjyiqi.com/rczp/"),
    ("北京排水集团", "https://www.bdc.cn/rczp/"),
    ("北京自来水集团", "https://www.bjwatergroup.com.cn/rczp/"),
    ("北京燃气集团", "https://www.bjgas.com/rczp/"),
    ("北京热力集团", "https://www.bdhg.com.cn/rczp/"),
    ("北京市保障性住房建设投资中心", "https://www.bphc.com.cn/rczp/"),
    ("北京水务投资中心", "https://www.bjwtz.com.cn/rczp/"),
]
for name, url in SOES:
    JOB_SOURCES.append((f"{name}-招聘", url, "国有企业", "北京国企"))

# ---------- 六、中央在京单位/国家级招聘平台（约 25 个） ----------
CENTRAL = [
    ("中央和国家机关所属事业单位招聘平台", "https://www.mohrss.gov.cn/SYrlzyhshbzb/fwyd/SYkaoshizhaopin/zyhgjjgsydwgkzp/", "事业单位", "中央在京"),
    ("中国公共招聘网", "https://job.mohrss.gov.cn/", "综合招聘", "中央在京"),
    ("人力资源社会保障部-事业单位人事管理", "https://www.mohrss.gov.cn/SYrlzyhshbzb/zwgk/sydwzp/", "事业单位", "中央在京"),
    ("国家公务员局", "https://www.scs.gov.cn/", "公务员", "中央在京"),
    ("国资委-央企招聘", "https://www.sasac.gov.cn/n2588035/n2588325/n2588350/index.html", "国有企业", "中央在京"),
    ("中科院人才招聘", "https://www.cas.cn/rcjy/zp/", "科研机构", "中央在京"),
    ("中国社会科学院招聘", "https://www.cass.cn/rczp/", "科研机构", "中央在京"),
    ("中国工程院招聘", "https://www.cae.cn/cae/html/main/col19/column_19_1.html", "科研机构", "中央在京"),
    ("国家自然科学基金委员会招聘", "https://www.nsfc.gov.cn/publish/portal0/tab670/", "科研机构", "中央在京"),
    ("教育部人才招聘", "https://www.moe.gov.cn/jyb_xwfb/gzdt_gzdt/s5987/202504/", "教育系统", "中央在京"),
    ("国家卫健委人才交流服务中心", "https://www.21wecan.com/rczp/", "医疗卫生", "中央在京"),
    ("中国疾病预防控制中心招聘", "https://www.chinacdc.cn/zxdt/tzgg/", "医疗卫生", "中央在京"),
    ("中国医学科学院北京协和医学院招聘", "https://www.pumc.edu.cn/renshichu/zpxx.htm", "医疗卫生", "中央在京"),
    ("国家博物馆招聘", "https://www.chnmuseum.cn/zpgg/", "文化事业", "中央在京"),
    ("故宫博物院招聘", "https://www.dpm.org.cn/zpxx/", "文化事业", "中央在京"),
    ("国家图书馆招聘", "https://www.nlc.cn/gygt/rczp/", "文化事业", "中央在京"),
    ("中央广播电视总台招聘", "https://www.cctv.com/rczp/", "传媒", "中央在京"),
    ("新华社招聘", "https://job.xinhua.org/", "传媒", "中央在京"),
    ("人民日报社招聘", "https://www.people.com.cn/GB/32306/419738/index.html", "传媒", "中央在京"),
    ("中国气象局招聘", "https://www.cma.gov.cn/2011xwzx/2011xrsxx/2011xzpgg/", "事业单位", "中央在京"),
    ("国家市场监督管理总局招聘", "https://www.samr.gov.cn/zwgk/rsks/", "事业单位", "中央在京"),
    ("国家药品监督管理局招聘", "https://www.nmpa.gov.cn/xxgk/ggtg/qtggtg/", "事业单位", "中央在京"),
    ("国聘网-央企招聘平台", "https://www.iguopin.com/", "国有企业", "中央在京"),
    ("中国国际航空公司招聘", "https://www.airchina.com.cn/cn/about/recruitment/", "国有企业", "中央在京"),
    ("中国航天科技集团招聘", "https://www.spacechina.com/n25/n144/index.html", "国有企业", "中央在京"),
]
JOB_SOURCES.extend(CENTRAL)

print(f"[INFO] 已加载招聘来源 URL 总数：{len(JOB_SOURCES)}")


# ============================================================
# 通用爬虫模块
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# 招聘关键词（用于识别招聘链接）
JOB_KEYWORDS = re.compile(
    r'(招聘|招贤|诚聘|引才|英才|校园招聘|社会招聘|公开招聘|事业单位招聘|'
    r'人才引进|人才招聘|用工|人员招聘|招录|聘用|岗位|校招|社招|公告|'
    r'通知|zhaopin|zpgg|rczp|notice|career|jobs?|recruit|employment|hr\b)',
    re.IGNORECASE
)

# 过滤无关链接的黑名单
EXCLUDE_PATTERNS = re.compile(
    r'(javascript:|mailto:|#|javascript|void\(0\)|css|js\?|login|register|'
    r'下载|pdf|doc|docx|xls|xlsx|zip|rar|png|jpg|jpeg|gif|ico|css|js$|'
    r'首页|下一页|上一页|末页|尾页|更多|more|More|MORE|返回|关闭|print)',
    re.IGNORECASE
)

# 薪资关键词提取
SALARY_PATTERNS = [
    (re.compile(r'(\d{1,3})\s*[kK万]?[-~至到]\s*(\d{1,3})\s*[kK万]'), lambda m: f"{m.group(1)}k-{m.group(2)}k"),
    (re.compile(r'月薪\s*(\d{1,5})\s*[-~至]\s*(\d{1,5})'), lambda m: f"{int(m.group(1))//1000}k-{int(m.group(2))//1000}k"),
    (re.compile(r'(\d{1,3})\s*[kK]\s*以上'), lambda m: f"{m.group(1)}k以上"),
    (re.compile(r'年薪\s*(\d{2,3})\s*万'), lambda m: f"{m.group(1)}w/年"),
    (re.compile(r'(\d{2,3})\s*万元?/年'), lambda m: f"{m.group(1)}w/年"),
]

# 地区关键词
DISTRICT_KEYWORDS = {
    "东城": "东城区", "西城": "西城区", "朝阳": "朝阳区", "海淀": "海淀区",
    "丰台": "丰台区", "石景山": "石景山区", "通州": "通州区", "大兴": "大兴区",
    "昌平": "昌平区", "顺义": "顺义区", "房山": "房山区", "门头沟": "门头沟区",
    "怀柔": "怀柔区", "平谷": "平谷区", "密云": "密云区", "延庆": "延庆区",
    "经开区": "经开区", "亦庄": "经开区", "北京": "北京市",
}

# 岗位类型关键词
TYPE_KEYWORDS = {
    "教师": "教育系统", "老师": "教育系统", "教授": "高校招聘", "讲师": "高校招聘",
    "医生": "医疗卫生", "医师": "医疗卫生", "护士": "医疗卫生", "护理": "医疗卫生",
    "工程师": "技术岗位", "程序员": "技术岗位", "开发": "技术岗位", "算法": "技术岗位",
    "会计": "财务岗位", "财务": "财务岗位", "出纳": "财务岗位",
    "管理": "管理岗位", "经理": "管理岗位", "主管": "管理岗位", "总监": "管理岗位",
    "行政": "行政岗位", "人事": "行政岗位", "HR": "行政岗位",
    "销售": "销售岗位", "市场": "市场岗位", "营销": "市场岗位",
    "实习": "实习岗位", "应届": "校园招聘", "校招": "校园招聘",
    "事业编": "事业单位", "编制": "事业单位", "国企": "国有企业",
}

# 日期提取
DATE_PATTERN = re.compile(r'(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})')


def safe_request(url, timeout=10):
    """安全的 HTTP 请求，带随机延迟和错误处理"""
    try:
        time.sleep(random.uniform(0.1, 0.4))
        resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False, allow_redirects=True)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        pass
    return None


def extract_jobs_from_page(html, base_url, source_name, source_cat, source_region):
    """从单个页面 HTML 中提取岗位链接和信息（通用策略）"""
    jobs = []
    if not html:
        return jobs

    try:
        soup = BeautifulSoup(html, 'html.parser')
    except Exception:
        return jobs

    # 移除 script/style/nav/footer
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript']):
        tag.decompose()

    # 策略1: 找列表中的 <a> 标签（最通用）
    seen_urls = set()
    all_links = soup.find_all('a', href=True)

    for a in all_links:
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not href or not text:
            continue
        if len(text) < 4 or len(text) > 120:
            continue
        if EXCLUDE_PATTERNS.search(text) or EXCLUDE_PATTERNS.search(href):
            # 但如果文字里包含招聘关键词，可能是有效内容
            if not JOB_KEYWORDS.search(text):
                continue

        # 链接必须包含招聘相关关键词（文字或URL路径中）
        if not (JOB_KEYWORDS.search(text) or JOB_KEYWORDS.search(href)):
            continue

        # 构建完整 URL
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme not in ('http', 'https'):
            continue
        # 只保留同域或近域链接（避免跳到完全无关站点）
        base_domain = urlparse(base_url).netloc
        link_domain = parsed.netloc
        if base_domain != link_domain and not (
            link_domain.endswith(base_domain.split('.')[-2] + '.' + base_domain.split('.')[-1])
            if len(base_domain.split('.')) >= 2 else False
        ):
            # 允许 .gov.cn / .edu.cn 内部跳转
            if not (base_domain.split('.')[-2:] == link_domain.split('.')[-2:]):
                continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # 智能提取元信息
        job_info = extract_job_metadata(text, full_url, source_name, source_cat, source_region, soup)
        if job_info:
            jobs.append(job_info)

    # 策略2: 如果策略1提取不足5条，尝试找列表项 <li>/<tr> 中的链接
    if len(jobs) < 5:
        for container in soup.find_all(['li', 'tr', 'div'], class_=re.compile(r'(item|list|news|article|notice|zp|job)', re.I)):
            a_tag = container.find('a', href=True)
            if not a_tag:
                continue
            text = a_tag.get_text(strip=True)
            href = a_tag['href']
            if not text or len(text) < 4 or len(text) > 120:
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ('http', 'https'):
                continue
            if full_url in seen_urls:
                continue

            if JOB_KEYWORDS.search(text) or JOB_KEYWORDS.search(href):
                seen_urls.add(full_url)
                job_info = extract_job_metadata(text, full_url, source_name, source_cat, source_region, soup)
                if job_info:
                    jobs.append(job_info)

    return jobs


def extract_job_metadata(title, url, source_name, source_cat, source_region, soup=None):
    """从标题、URL和页面中提取岗位元信息"""
    # 清理标题
    title = re.sub(r'[\[\]【】\(\)（）\s]+', ' ', title).strip()
    title = re.sub(r'\s+', ' ', title)
    if not title or len(title) < 4:
        return None

    # 1. 地区识别（先从标题匹配，再用来源默认值；非行政区的来源默认"北京市"）
    region = "北京市"
    DISTRICT_VALUES = set(DISTRICT_KEYWORDS.values())
    if source_region in DISTRICT_VALUES:
        region = source_region
    for kw, dist in DISTRICT_KEYWORDS.items():
        if kw in title:
            region = dist
            break

    # 2. 薪资识别
    salary = "薪资面议"
    for pattern, formatter in SALARY_PATTERNS:
        m = pattern.search(title)
        if m:
            salary = formatter(m)
            break
    if salary == "薪资面议" and soup:
        # 尝试在页面文本中找薪资
        page_text = soup.get_text()
        for pattern, formatter in SALARY_PATTERNS:
            m = pattern.search(page_text)
            if m:
                salary = formatter(m)
                break

    # 3. 岗位类型
    job_type = source_cat
    for kw, t in TYPE_KEYWORDS.items():
        if kw in title:
            job_type = t
            break

    # 4. 日期识别
    pub_date = ""
    m = DATE_PATTERN.search(title)
    if m:
        pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    elif soup:
        # 在页面中找日期
        page_text = soup.get_text()[:5000]
        m = DATE_PATTERN.search(page_text)
        if m:
            pub_date = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

    # 5. 推断招聘单位
    company = source_name.replace("-招聘", "").replace("-人才招聘", "").replace("-通知公告", "").replace("-企业招聘", "")

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
    """爬取单个来源，返回该来源的岗位列表"""
    name, url, cat, region = source
    jobs = []
    html = safe_request(url)
    if html:
        jobs = extract_jobs_from_page(html, url, name, cat, region)
        # 如果该页支持翻页（index_1.html 等），尝试再抓一页
        parsed = urlparse(url)
        path = parsed.path
        if path.endswith('/') or path.endswith('.htm') or path.endswith('.html'):
            # 推断翻页URL
            base_path = path.rstrip('/')
            if base_path.endswith('.html') or base_path.endswith('.htm'):
                # zpxx.htm -> zpxx_1.htm? or index_1.html
                m = re.search(r'(.+?)(?:_(\d+))?\.(html?)$', base_path)
                if m:
                    prefix = m.group(1)
                    ext = m.group(3)
                    for page_idx in range(1, 3):
                        next_url = f"{parsed.scheme}://{parsed.netloc}{prefix}_{page_idx}.{ext}"
                        if next_url == url:
                            continue
                        html2 = safe_request(next_url)
                        if html2:
                            jobs.extend(extract_jobs_from_page(html2, next_url, name, cat, region))
                        else:
                            break
            elif path.endswith('/'):
                # 目录页：尝试 index_1.html, index_2.html
                for page_idx in range(1, 3):
                    next_url = f"{url.rstrip('/')}/index_{page_idx}.html"
                    html2 = safe_request(next_url)
                    if html2:
                        jobs.extend(extract_jobs_from_page(html2, next_url, name, cat, region))
                    else:
                        break
    return name, jobs


def crawl_all_sources(max_workers=20, max_sources=None):
    """并发爬取所有来源"""
    sources = JOB_SOURCES[:max_sources] if max_sources else JOB_SOURCES
    all_jobs = []
    source_stats = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_source = {
            executor.submit(crawl_single_source, src): src
            for src in sources
        }
        for future in as_completed(future_to_source):
            src = future_to_source[future]
            try:
                name, jobs = future.result(timeout=20)
                source_stats[name] = len(jobs)
                all_jobs.extend(jobs)
            except Exception as e:
                source_stats[src[0]] = 0

    return all_jobs, source_stats


def filter_jobs(jobs, keyword="", region="", salary="", job_type=""):
    """根据条件过滤岗位"""
    filtered = []

    # 解析薪资下限
    min_salary = 0
    if salary:
        m = re.search(r'(\d+)', salary)
        if m:
            min_salary = int(m.group(1))

    keywords = [k.strip() for k in keyword.split() if k.strip()] if keyword else []

    for job in jobs:
        # 关键词过滤（岗位标题/单位/类型中匹配任一关键词）
        if keywords:
            text = (job.get("title", "") + job.get("company", "") + job.get("type", "")).lower()
            if not all(kw.lower() in text for kw in keywords):
                continue

        # 地区过滤
        if region and region != "北京市":
            if job.get("location", "") != region and region not in job.get("location", ""):
                continue

        # 薪资过滤
        if min_salary > 0:
            salary_text = job.get("salary", "")
            sal_nums = [int(n) for n in re.findall(r'(\d+)', salary_text)]
            if sal_nums:
                max_sal = max(sal_nums)
                # "8k-15k" 里如果 15 >= min_salary 则保留；"10k以上" 直接保留
                if "以上" in salary_text:
                    pass  # 满足
                elif max_sal < min_salary:
                    continue
            else:
                # 未提取到薪资的岗位在设置了薪资要求时过滤掉
                continue

        # 类型过滤
        if job_type and job_type != "不限":
            if job_type not in job.get("type", "") and job.get("type", "") not in job_type:
                continue

        filtered.append(job)

    return filtered


def deduplicate_and_sort(jobs):
    """去重 + 按日期排序"""
    seen = set()
    unique = []
    for job in jobs:
        url = job.get("url", "")
        title = job.get("title", "")
        key = url if url else title
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)

    # 按日期倒序，无日期的放后面
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


@app.route('/api/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        data = request.json or {}
    else:
        data = request.args

    keyword = (data.get('keyword') or data.get('position') or '').strip()
    region = (data.get('region') or '').strip()
    salary = (data.get('salary') or '').strip()
    job_type = (data.get('type') or data.get('job_type') or '').strip()

    print(f"[API] 搜索请求：关键词='{keyword}' 地区='{region}' 薪资='{salary}' 类型='{job_type}'")

    # 爬取所有来源
    t0 = time.time()
    all_jobs, stats = crawl_all_sources(max_workers=25)
    crawl_time = time.time() - t0

    # 过滤
    filtered = filter_jobs(all_jobs, keyword=keyword, region=region, salary=salary, job_type=job_type)
    # 去重排序
    filtered = deduplicate_and_sort(filtered)
    # 限制返回数量（避免前端卡死）
    result = filtered[:500]

    # 统计信息
    success_sources = sum(1 for v in stats.values() if v > 0)
    total_sources = len(stats)

    print(f"[API] 爬取完成：{total_sources}个来源中{success_sources}个成功，"
          f"共{len(all_jobs)}条岗位，筛选后{len(filtered)}条，返回{len(result)}条，耗时{crawl_time:.1f}s")

    return jsonify({
        "jobs": result,
        "stats": {
            "total_sources": total_sources,
            "success_sources": success_sources,
            "total_crawled": len(all_jobs),
            "total_matched": len(filtered),
            "returned": len(result),
            "crawl_time": round(crawl_time, 1),
            "source_detail": {k: v for k, v in sorted(stats.items(), key=lambda x: -x[1]) if v > 0},
        }
    })


@app.route('/api/sources', methods=['GET'])
def get_sources():
    """返回所有来源URL列表（供用户查看）"""
    categories = {}
    for name, url, cat, region in JOB_SOURCES:
        cat_key = f"{region} - {cat}" if region != cat else cat
        if cat_key not in categories:
            categories[cat_key] = []
        categories[cat_key].append({"name": name, "url": url})
    return jsonify({
        "total": len(JOB_SOURCES),
        "categories": categories,
    })


if __name__ == '__main__':
    # 禁用 SSL 警告
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    print("=" * 60)
    print("  北京岗位信息查找系统 - 启动中")
    print(f"  招聘来源总数：{len(JOB_SOURCES)} 个")
    print("  访问地址：http://127.0.0.1:5000/")
    print("=" * 60)
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)
