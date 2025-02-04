#!/usr/bin/env python
# coding=utf8
# author=evi1m0
# website=linux.im

'''
    12306 Captcha Picture:
    author: joeyxy83@gmail.com
        1. Download Captcha
        2. Pic Conver Text
        3. Return result
'''

import re
import time
import json
import urllib
import urllib2
import requests
from PIL import Image
from xml.etree import ElementTree
import httplib

global text

__version__ = '0.1'
# version for Python OcrKing Client #

# OcrKing Api Url #
__api_url__ = 'http://lab.ocrking.com/ok.html'


def downloadImg():
    pic_file = int(time.time())
    pic_url = "https://kyfw.12306.cn/otn/passcodeNew/getPassCodeNew?module=login&rand=sjrand"
    print '[+] Download Picture: {}'.format(pic_url)
    try:
        resp = requests.get(pic_url, verify=False, timeout=5)
    except:
        resp = requests.get(pic_url, verify=False, timeout=3)
    with open("./12306_pic/%s.jpg"%pic_file, 'wb') as fp:
        fp.write(resp.content)
    return pic_file

def imgCut():
    pic_file = downloadImg()
    pic_path = "./12306_pic/%s.jpg" % pic_file
    pic_text_path = './12306_pic/%s_text.jpg' % pic_file
    pic_obj = Image.open(pic_path)
    box = (120,0,290,25)
    region = pic_obj.crop(box)
    region.save(pic_text_path)
    print '[*] Picture Text Picture: {}'.format(pic_text_path)
    return pic_path, pic_text_path

def ocrApi(filename):
    # Text picture conver text.
    upload_pic_url = "http://cn.docs88.com/pdftowordupload2.php"
    headers_fake = {
            'ccept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6',
            'Connection': 'keep-alive',
            'Host': 'cn.docs88.com',
            'Origin': 'http://cn.docs88.com',
            'User-Agent': 'Mozilla/5.0 (KHTML, like Gecko) Chrome/41.0.2272.89',
            'X-Requested-With': 'ShockwaveFlash/17.0.0.134',
            }
    filename_tmp = filename.split('/')[-1]
    pic_text_content = open(filename).read()
    para = {'Filename': filename_tmp,
           'sourcename': filename_tmp,
           'sourcelanguage': 'cn',
           'desttype': 'txt',
           'Upload': 'Submit Query',}
    upload_pic = requests.post(upload_pic_url, data=para, files={"Filedata" : open(filename, 'rb')}, headers=headers_fake)
    time.sleep(2)
    text_result_url = 'http://cn.docs88.com/' + upload_pic.content[3:]
    text_result = requests.get(text_result_url)
    if text_result.status_code == 200:
        print '[*] Text: {}'.format(text_result.content)
    else:
        print '[-] False'
    return text_result.content






def post_multipart(fields, files):
    content_type, body = encode_multipart(fields, files)
    httplib.HTTPConnection._http_vsn = 10  #must using the http 1.0
    httplib.HTTPConnection._http_vsn_str = 'HTTP/1.0'
    headers = {'Content-Type': content_type,
    'Content-Length': str(len(body)),
    'Accept-Encoding': 'gzip, deflate',
    'Referer': 'http://lab.ocrking.com/?pyclient' + __version__,
    'Accept':'*/*'}
    r = urllib2.Request(__api_url__, body, headers)
    return urllib2.urlopen(r).read()

### format the data into multipart/form-data ###    
def encode_multipart(fields, files):
    LIMIT = '----------OcrKing_Client_Aven_s_Lab_L$'
    CRLF = '\r\n'
    L = []
    for (key, value) in fields:
        L.append('--' + LIMIT)
        L.append('Content-Disposition: form-data; name="%s"' % key)
        L.append('')
        L.append(value)
    for (key, filename, value) in files:
        L.append('--' + LIMIT)
        L.append('Content-Disposition: form-data; name="%s"; filename="%s"' % (key, filename))
        L.append('Content-Type: application/octet-stream' )
        L.append('')
        L.append(value)
    L.append('--' + LIMIT + '--')
    L.append('')
    body = CRLF.join(L)
    content_type = 'multipart/form-data; boundary=%s' % LIMIT
    return content_type, body

def print_node(node):
    print "====="
    for key,value in node.items():
        print "%s:%s" % (key, value)
    for subnode in node.getchildren():
        if subnode.tag == 'Result':
            print "%s:%s" % (subnode.tag, subnode.text)
            global text 
            text = subnode.text
        if subnode.tag == 'Status':
            print "%s:%s" % (subnode.tag, subnode.text) 
   


def read_xml(text = '', xmlfile = ''):
    root = ElementTree.fromstring(text)
    eitor = root.getiterator("Item")
    for e in eitor:
        print_node(e)


def ocrApi2(filename):
    file = filename.split('/')[-1]
    key = 'def1e763f8ebc690cbubfgyKfTjB0MI1egCtrNt4hJNBlxyCRy1bptAc65x1VhI0dCcwTL'
    #print file,path
    image = open(filename, "rb") 
    file = [('ocrfile',file,image.read())]

    fields = [('url',''),('service', 'OcrKingForCaptcha'),('language','sim'),('charset',''),('apiKey', key),('type','https://kyfw.12306.cn/otn/passcodeNew/getPassCodeNew?module=login&rand=sjrand')]

    xml = post_multipart(fields,file)
    read_xml(xml)
    #print xml


'''
    baidu stu
    author: andelf
'''
def baidu_stu_html_extract(html):
    pattern = re.compile(r"keywords:'(.*?)'")
    matches = pattern.findall(html)
    if not matches:
        return '[UNKOWN]'
    json_str = matches[0]
    json_str = json_str.replace('\\x22', '"').replace('\\\\', '\\')
    result = [item['keyword'] for item in json.loads(json_str)]
    return '|'.join(result) if result else '[UNKOWN]'

def baidu_stu_lookup(im):
    url = ("http://stu.baidu.com/n/image?fr=html5&needRawImageUrl=true&id="
          "WU_FILE_0&name=233.png&type=image%2Fpng&lastModifiedDate=Mon+Mar"
          "+16+2015+20%3A49%3A11+GMT%2B0800+(CST)&size=")
    im.save("./query_temp_img.png")
    raw = open("./query_temp_img.png", 'rb').read()
    url = url + str(len(raw))
    req = urllib2.Request(url, raw, {'Content-Type':'image/png', 'User-Agent':UA})
    resp = urllib2.urlopen(req)
    resp_url = resp.read()      # return a pure url
    url = "http://stu.baidu.com/n/searchpc?queryImageUrl=" + urllib.quote(resp_url)
    req = urllib2.Request(url, headers={'User-Agent':UA})
    resp = urllib2.urlopen(req)
    html = resp.read()
    return baidu_stu_html_extract(html)

def get_sub_img(pic_text_path, x, y):
    im = Image.open(pic_text_path)
    assert 0 <= x <= 3
    assert 0 <= y <= 2
    WITH = HEIGHT = 68
    left = 5 + (67 + 5) * x
    top = 41 + (67 + 5) * y
    right = left + 67
    bottom = top + 67
    return im.crop((left, top, right, bottom))


if __name__ == '__main__':
    UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.89 Safari/537.36"
    pic_path, pic_text_path = imgCut()
    ocrApi2(pic_text_path)
    global  text
    captcha_text = text
    dict_list = {}
    count = 0
    for y in range(2):
        for x in range(4):
            count += 1
            im2 = get_sub_img(pic_path, x, y)
            result = baidu_stu_lookup(im2)
            dict_list[count] = result
            print (y,x), result
    if captcha_text.strip() > 2:
        print '\n[*] Maybe the result of the:'
        maybe_result = []
        for v in dict_list:
            for c in range(len(unicode(captcha_text.strip(), 'utf8'))):
                text = unicode(captcha_text, 'utf8')[c]
                if text in dict_list[v]:
                    _str_res = '%s --- %s' % (v, dict_list[v])
                    maybe_result.append(_str_res)
        for r in list(set(maybe_result)):
            print r
    else:
        print '[-] False'
