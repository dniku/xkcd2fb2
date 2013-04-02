# -*- coding: utf-8 -*-
import os
import urllib, urllib2
import json
import datetime, uuid
import base64
import contextlib, cStringIO, zipfile
from bs4 import BeautifulSoup
import Image

comics_dir = 'comics'
output_dir = 'output'

filenames_filename = 'filenames.json'
titles_filename    = 'titles.json'
comments_filename  = 'comments.json'

skipped_comics = set((404, ))
# Guest Week: Zach Weiner (SMBC) (#826) contains way more than
# just one image.
# Umwelt (#1037) is a comic that can be normally viewed only
# from a browser since the image is loaded dynamically and is
# based on a number of parameters such as your referer, browser,
# location, ISP, etc.
# Click and Drag (#1110) is also hard to embed in e-book format.

comics_per_file = 1000
create_zip = True

def get_soup(address):
    response = urllib2.urlopen(address)
    html = response.read()
    soup = BeautifulSoup(html)
    return soup

def get_number_of_comics():
    soup = get_soup('http://xkcd.com/')
    a = soup.find('a', rel='prev')
    prev = a['href'][1:-1]
    return int(prev) + 1

def download_comic(number, filenames, titles, comments):
    if number in skipped_comics:
        return False
    if (number in filenames
     and number in titles
     and number in comments
     and os.path.exists(os.path.join(comics_dir, filenames[number]))):
        return False

    try:
        soup = get_soup('http://xkcd.com/%d/' % number)
    except urllib2.URLError as e:
        print 'Failed to download the page for comic #%d, it will be ignored from now on...'
        skipped_comics.add(number)
        return False
        
    comic = soup.find('div', id='comic')

    comic_src = comic.img['src']
    comic_filename = comic_src.split('/')[-1]
    comic_path = os.path.join(comics_dir, comic_filename)
    if not os.path.exists(comic_path):
        print 'Downloading comic #%d: %s...' % (number, comic_filename),
        urllib.urlretrieve(comic_src, comic_path)
        print 'done.'

    # For #472: <span style="color: #0000ED">House</span> of Pancakes
    comic_title   = u''.join(soup.find('div', id='ctitle').strings)
    comic_comment = unicode(comic.img['title'])

    filenames[number] = comic_filename
    titles[number]    = comic_title
    comments[number]  = comic_comment

    return True

def load_dictionary(src_filename):
    result = {}
    if os.path.exists(src_filename):
        with open(src_filename, 'r') as src_file:
            result.update({int(k): v for k, v in json.load(src_file).iteritems()})
    return result

def save_dictionary(dst_filename, dictionary):
    with open(dst_filename, 'w') as dst_file:
        json.dump(dictionary, dst_file, sort_keys=True, indent=4)

def download_comics(number_from, number_to):
    filenames = load_dictionary(filenames_filename)
    titles    = load_dictionary(titles_filename)
    comments  = load_dictionary(comments_filename)

    downloaded_something = False
    for number in xrange(number_from, number_to + 1):
        downloaded_something |= download_comic(number, filenames, titles, comments)

    save_dictionary(filenames_filename, filenames)
    save_dictionary(titles_filename, titles)
    save_dictionary(comments_filename, comments)

    return downloaded_something, filenames, titles, comments

header_template = u'''\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description>
  <title-info>
    <genre>humor</genre>
    <author>
      <first-name>Randall</first-name>
      <last-name>Munroe</last-name>
    </author>
    <book-title>xkcd {comic_from}–{comic_to}</book-title>
    <annotation>
      <p>A webcomic of romance, sarcasm, math, and language.</p>
    </annotation>
    <lang>en</lang>
    <sequence name="xkcd" number="{sequence_number}"/>
  </title-info>
  <document-info>
    <author>
      <nickname>Pastafarianist</nickname>
    </author>
    <date value="{today_iso}">{today_human}</date>
    <src-url>http://xkcd.com/</src-url>
    <id>{document_id}</id>
    <version>1.0</version>
  </document-info>
</description>
<body>
'''
def write_header(file_obj, comic_from, comic_to, sequence_number):
    document_id = str(uuid.uuid4())
    today = datetime.date.today()
    today_iso = today.isoformat()
    today_human = today.strftime('%d %B %Y')
    header = header_template.format(**locals()) # today_iso, today_human, document_id, comic_from, comic_to, sequence_number
    file_obj.write(header.encode('utf-8'))

def fix_filename(filename):
    filename = filename.replace('(', 'lbr').replace(')', 'rbr')
    if '0' <= filename[0] <= '9':
        filename = '_' + filename
    return unicode(filename)

section_template = u'''\
  <section>
    <title><p>{number}: {title}</p></title>
    <image l:href="#{fixed_filename}"/>
    <p>{comment}</p>
  </section>
'''
def write_section(file_obj, number, title, filename, comment):
    # FB2 does not support GIF.
    # Therefore, all *.gif images have to be converted.
    # The conversion itself will be performed later.
    # But we're changing the filename the comic refers to to reflect that the image will no longer be in GIF format.
    
    basename, extension = os.path.splitext(filename)
    if extension == '.gif':
        extension = '.png'
        filename = basename + extension
    
    section = section_template.format(fixed_filename=fix_filename(filename), **locals()) # number, title, comment
    file_obj.write(section.encode('utf-8'))

binary_template = u'''\
<binary id="{fixed_filename}" content-type="{content_type}">
{binary_data}
</binary>
'''
def write_binary(file_obj, filename):
    basename, extension = os.path.splitext(filename)
    filepath = os.path.join(comics_dir, filename)

    if extension == '.gif': # From #961
        im = Image.open(filepath)
        extension = '.png'
        filename = basename + extension
        filepath = os.path.join(comics_dir, filename)
        im.save(filepath)

    content_type = {'.jpg': u'image/jpeg', '.png': u'image/png'}[extension]
    with open(filepath, 'rb') as f:
        binary_data = base64.b64encode(f.read())
    binary = binary_template.format(fixed_filename=fix_filename(filename), **locals()) # content_type, binary_data)
    file_obj.write(binary.encode('utf-8'))

def make_fb2(buff, comic_from, comic_to, sequence_number, force_build):
    print 'Downloading comics %d-%d...' % (comic_from, comic_to),
    downloaded_something, filenames, titles, comments = download_comics(comic_from, comic_to)
    if downloaded_something:
        print 'download complete.'
    else:
        print 'nothing new was downloaded.'
        if not force_build:
            return False
    print 'Building FB2 in-memory...',
    write_header(buff, comic_from, comic_to, sequence_number)
    for number in xrange(comic_from, comic_to + 1):
        if number in skipped_comics:
            continue
        write_section(buff, number, titles[number], filenames[number], comments[number])
    buff.write(u'</body>\n')
    for number in xrange(comic_from, comic_to + 1):
        if number in skipped_comics:
            continue
        write_binary(buff, filenames[number])
    buff.write(u'</FictionBook>')
    print 'done.'
    return True


if __name__ == '__main__':
    if not os.path.exists(comics_dir):
        os.makedirs(comics_dir)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    total_comics = get_number_of_comics()
    number_length = len(str(total_comics)) # Don't want to import math
    fb2_filename_template = 'xkcd_%0{n}d-%0{n}d.fb2'.format(n=number_length)
    
    for comic_from in xrange(1, total_comics + 1, comics_per_file):
        comic_to       = min(total_comics, comic_from + comics_per_file - 1)
        sequence_index = (comic_from + comics_per_file - 1) // comics_per_file
        book_filename  = fb2_filename_template % (comic_from, comic_to)
        book_path      = os.path.join(output_dir, book_filename)

        if create_zip:
            book_filename = book_filename + '.zip'
            book_path     = book_path + '.zip'

        force_build = not os.path.exists(book_path)

        with contextlib.closing(cStringIO.StringIO()) as buff:
            success = make_fb2(buff, comic_from, comic_to, sequence_index, force_build)
            
            if not success:
                continue
        
            print 'Writing %s...' % book_path,
            if create_zip:
                with zipfile.ZipFile(book_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    zip_file.writestr(book_filename, buff.getvalue())
            else:
                with open(book_path, 'w') as fb2_file:
                    fb2_file.write(buff.getvalue())
        print 'done.'
