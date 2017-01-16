# -*- coding: utf-8 -*-
"""
/***************************************************************************
 TileLayer Plugin
                                 A QGIS plugin
 Plugin layer for Tile Maps
                              -------------------
        begin                : 2012-12-16
        copyright            : (C) 2013 by Minoru Akagi
        email                : akaginch@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import math
import ast
import operator as op

from PyQt4.QtCore import QRect
from PyQt4.QtGui import QImage, QPainter
from qgis.core import QgsRectangle
from re import sub

# supported operators
operators = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Mod: op.mod,
             ast.Div: op.truediv, ast.Pow: op.pow, ast.BitXor: op.xor,
             ast.USub: op.neg, ast.FloorDiv: op.floordiv}

def eval_expr(expr):
    #expression format is: {arithmetic expression[,zero-padding]}  
    elems = expr.group(0)[1:-1].split(',')
        padding = elems[1]
    else:
        padding = '0'
    expr = elems[0]
    return format((eval_(ast.parse(expr, mode='eval').body)),'0'+padding)

def eval_(node):
    if isinstance(node, ast.Num): # <number>
        return node.n
    elif isinstance(node, ast.BinOp): # <left> <operator> <right>
        return operators[type(node.op)](eval_(node.left), eval_(node.right))
    elif isinstance(node, ast.UnaryOp): # <operator> <operand> e.g., -1
        return operators[type(node.op)](eval_(node.operand))
    else:
        raise TypeError(node)

R = 6378137

class TileDefaultSettings:

  ZMIN = 0
  ZMAX = 18

def degreesToMercatorMeters(lon, lat):
  # formula: http://en.wikipedia.org/wiki/Mercator_projection#Mathematics_of_the_Mercator_projection
  x = R * lon * math.pi / 180
  y = R * math.log(math.tan((90 + lat) * math.pi / 360))
  return x, y


class BoundingBox:

  def __init__(self, xmin, ymin, xmax, ymax):
    self.xmin = xmin
    self.ymin = ymin
    self.xmax = xmax
    self.ymax = ymax

  def toQgsRectangle(self):
    return QgsRectangle(self.xmin, self.ymin, self.xmax, self.ymax)

  def toString(self, digitsAfterPoint=None):
    if digitsAfterPoint is None:
      return "%f,%f,%f,%f" % (self.xmin, self.ymin, self.xmax, self.ymax)
    return "%.{0}f,%.{0}f,%.{0}f,%.{0}f".format(digitsAfterPoint) % (self.xmin, self.ymin, self.xmax, self.ymax)

  @classmethod
  def degreesToMercatorMeters(cls, bbox):
    xmin, ymin = degreesToMercatorMeters(bbox.xmin, bbox.ymin)
    xmax, ymax = degreesToMercatorMeters(bbox.xmax, bbox.ymax)
    return BoundingBox(xmin, ymin, xmax, ymax)

  @classmethod
  def fromString(cls, s):
    a = map(float, s.split(","))
    return BoundingBox(a[0], a[1], a[2], a[3])


class TileLayerDefinition:

  TILE_SIZE = 256
  TSIZE1 = 20037508.342789244

  def __init__(self, title, attribution, serviceUrl, yOriginTop=1, zmin=TileDefaultSettings.ZMIN, zmax=TileDefaultSettings.ZMAX, bbox=None):
    self.title = title
    self.attribution = attribution
    self.serviceUrl = serviceUrl
    self.yOriginTop = yOriginTop
    self.zmin = max(zmin, 0)
    self.zmax = zmax
    self.bbox = bbox

  def tileUrl(self, zoom, x, y):
    if not self.yOriginTop:
      y = (2 ** zoom - 1) - y
    #replace x,y,z in url
    primary_url = self.serviceUrl.replace("{z}", str(zoom)).replace("{x}", str(x)).replace("{y}", str(y))
    #solve arithmetic expressions, if present
    return sub('{[^}]+}',eval_expr,primary_url)

  def getTileRect(self, zoom, x, y):
    size = self.TSIZE1 / 2 ** (zoom - 1)
    return QgsRectangle(x * size - self.TSIZE1, self.TSIZE1 - y * size, (x + 1) * size - self.TSIZE1, self.TSIZE1 - (y + 1) * size)

  def degreesToTile(self, zoom, lon, lat):
    x, y = degreesToMercatorMeters(lon, lat)
    size = self.TSIZE1 / 2 ** (zoom - 1)
    tx = int((x + self.TSIZE1) / size)
    ty = int((self.TSIZE1 - y) / size)
    return tx, ty

  def bboxDegreesToTileRange(self, zoom, bbox):
    xmin, ymin = self.degreesToTile(zoom, bbox.xmin, bbox.ymax)
    xmax, ymax = self.degreesToTile(zoom, bbox.xmax, bbox.ymin)
    return BoundingBox(xmin, ymin, xmax, ymax)

  def __str__(self):
    return "%s (%s)" % (self.title, self.serviceUrl)

  def toArrayForTreeView(self):
    extent = ""
    if self.bbox:
      extent = self.bbox.toString(2)
    return [self.title, self.attribution, self.serviceUrl, "%d-%d" % (self.zmin, self.zmax), extent, self.yOriginTop]

  @classmethod
  def createEmptyInfo(cls):
    return TileLayerDefinition("", "", "")


class Tile:
  def __init__(self, zoom, x, y, data=None):
    self.zoom = zoom
    self.x = x
    self.y = y
    self.data = data


class Tiles:

  def __init__(self, zoom, xmin, ymin, xmax, ymax, serviceInfo):
    self.zoom = zoom
    self.xmin = xmin
    self.ymin = ymin
    self.xmax = xmax
    self.ymax = ymax
    self.TILE_SIZE = serviceInfo.TILE_SIZE
    self.TSIZE1 = serviceInfo.TSIZE1
    self.yOriginTop = serviceInfo.yOriginTop
    self.serviceInfo = serviceInfo
    self.tiles = {}

  def addTile(self, url, tile):
    self.tiles[url] = tile

  def setImageData(self, url, data):
    if url in self.tiles:
      self.tiles[url].data = data

  def image(self):
    width = (self.xmax - self.xmin + 1) * self.TILE_SIZE
    height = (self.ymax - self.ymin + 1) * self.TILE_SIZE
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(image)
    for tile in self.tiles.values():
      if not tile.data:
        continue

      x = tile.x - self.xmin
      y = tile.y - self.ymin
      rect = QRect(x * self.TILE_SIZE, y * self.TILE_SIZE, self.TILE_SIZE, self.TILE_SIZE)

      timg = QImage()
      timg.loadFromData(tile.data)
      p.drawImage(rect, timg)
    return image

  def extent(self):
    size = self.TSIZE1 / 2 ** (self.zoom - 1)
    return QgsRectangle(self.xmin * size - self.TSIZE1, self.TSIZE1 - (self.ymax + 1) * size,
                        (self.xmax + 1) * size - self.TSIZE1, self.TSIZE1 - self.ymin * size)
