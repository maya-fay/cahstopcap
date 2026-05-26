from sqlalchemy import Column, Integer, String, Text
from database import Base

class ShowroomItem(Base):
    __tablename__ = "showroom_items"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    image_url = Column(String(500), nullable=False)
    display_order = Column(Integer, default=0)

class GalleryItem(Base):
    __tablename__ = "gallery_items"
    id = Column(Integer, primary_key=True, index=True)
    caption = Column(String(255))
    image_url = Column(String(500), nullable=False)
    display_order = Column(Integer, default=0)
