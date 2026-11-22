from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops
import math
ANTON='/usr/share/fonts/truetype/google-fonts/ofl/anton/Anton-Regular.ttf'
POP='/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf'
POPM='/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf'
RED=(232,64,64); BLUE=(60,140,255); BG=(11,11,15)

def glow(layer, r, strength=2):
    g=layer.filter(ImageFilter.GaussianBlur(r))
    out=g
    for _ in range(strength-1): out=ImageChops.add(out,g)
    return out

def emblem(S):
    """Mic whose grille holds a hooded shadow with red/blue eyes."""
    k=S/1000
    img=Image.new('RGBA',(S,S),(0,0,0,0))
    d=ImageDraw.Draw(img)
    cx=S//2
    # outer ring with red->blue split
    ring=Image.new('RGBA',(S,S),(0,0,0,0)); rd=ImageDraw.Draw(ring)
    rd.arc([40*k,40*k,S-40*k,S-40*k],100,260,fill=RED+(255,),width=int(22*k))
    rd.arc([40*k,40*k,S-40*k,S-40*k],-80,80,fill=BLUE+(255,),width=int(22*k))
    img=Image.alpha_composite(img,glow(ring,18*k,2)); img=Image.alpha_composite(img,ring)
    d=ImageDraw.Draw(img)
    d.ellipse([70*k,70*k,S-70*k,S-70*k],fill=(16,16,22,255))
    # sound wave arcs
    for i,rad in enumerate((330,380)):
        a=int(10*k)
        d.arc([cx-rad*k,430*k-rad*k,cx+rad*k,430*k+rad*k],150,210,fill=RED+(140-40*i,),width=a)
        d.arc([cx-rad*k,430*k-rad*k,cx+rad*k,430*k+rad*k],-30,30,fill=BLUE+(140-40*i,),width=a)
    # mic capsule (rounded) - the "hood"
    cap=[cx-190*k,170*k,cx+190*k,700*k]
    d.rounded_rectangle(cap,radius=int(190*k),fill=(30,30,40,255),outline=(70,70,90,255),width=int(10*k))
    # inner shadow face
    d.rounded_rectangle([cx-150*k,215*k,cx+150*k,660*k],radius=int(150*k),fill=(6,6,9,255))
    # grille lines (subtle)
    for y in range(240,650,34):
        d.line([cx-150*k,y*k,cx+150*k,y*k],fill=(22,22,30,255),width=int(4*k))
    # eyes
    eyes=Image.new('RGBA',(S,S),(0,0,0,0)); ed=ImageDraw.Draw(eyes)
    ey=400*k
    def eye(x,col):
        # angled almond eye
        pts=[(x-62*k,ey+6*k),(x-20*k,ey-20*k),(x+58*k,ey-8*k),(x+30*k,ey+20*k),(x-30*k,ey+22*k)]
        ed.polygon(pts,fill=col+(255,))
    eye(cx-80*k,RED); 
    # mirror for right eye
    pts=[(cx+80*k+62*k,ey+6*k),(cx+80*k+20*k,ey-20*k),(cx+80*k-58*k,ey-8*k),(cx+80*k-30*k,ey+20*k),(cx+80*k+30*k,ey+22*k)]
    ed.polygon(pts,fill=BLUE+(255,))
    img=Image.alpha_composite(img,glow(eyes,40*k,3))
    img=Image.alpha_composite(img,glow(eyes,12*k,2))
    img=Image.alpha_composite(img,eyes)
    d=ImageDraw.Draw(img)
    d.ellipse([cx-92*k,ey-8*k,cx-70*k,ey+10*k],fill=(255,225,225,255))
    d.ellipse([cx+70*k,ey-8*k,cx+92*k,ey+10*k],fill=(225,238,255,255))
    # mic yoke + stand
    d.arc([cx-240*k,420*k,cx+240*k,800*k],20,160,fill=(90,90,110,255),width=int(16*k))
    d.rectangle([cx-12*k,800*k,cx+12*k,860*k],fill=(90,90,110,255))
    d.rounded_rectangle([cx-110*k,850*k,cx+110*k,880*k],radius=int(14*k),fill=(90,90,110,255))
    return img

def solid_mask(size, xy, txt, font):
    m=Image.new('L',size,0); ImageDraw.Draw(m).text(xy,txt,font=font,fill=255)
    # fill any interior holes from overlapping glyph contours: flood background from the corner
    return m
def text_glow(canvas, xy, txt, font, col, r):
    m=solid_mask(canvas.size,xy,txt,font)
    lay=Image.new('RGBA',canvas.size,col+(0,)); lay.putalpha(m)
    g=lay.filter(ImageFilter.GaussianBlur(r)); g.putalpha(g.getchannel('A').point(lambda v:int(v*0.85)))
    canvas=Image.alpha_composite(canvas,g)
    return Image.alpha_composite(canvas,lay)

def spaced(d, x, y, txt, font, fill, spacing):
    for ch in txt:
        d.text((x,y),ch,font=font,fill=fill); x+=d.textlength(ch,font=font)+spacing
    return x
def spaced_w(d,txt,font,spacing): return sum(d.textlength(c,font=font)+spacing for c in txt)-spacing

SS=2
# ---------- horizontal logo 2400x1000 ----------
W,H=2400*SS,1000*SS
c=Image.new('RGBA',(W,H),BG+(255,))
vg=Image.new('L',(W,H),0); ImageDraw.Draw(vg).ellipse([-W*0.2,-H*0.6,W*1.2,H*1.6],fill=60)
c=Image.alpha_composite(c,Image.merge('RGBA',(vg.point(lambda v:v//3),vg.point(lambda v:v//3),vg.point(lambda v:v//2),vg)))
em=emblem(820*SS); c.alpha_composite(em,(90*SS,90*SS))
d=ImageDraw.Draw(c)
fA=ImageFont.truetype(ANTON,420*SS)
x0=980*SS; y0=40*SS
wS=d.textlength('S',font=fA); wI=d.textlength('I',font=fA)
c=text_glow(c,(x0,y0),'S',fA,RED,22*SS)
c=text_glow(c,(x0+wS+18*SS,y0),'I',fA,(236,236,242),3*SS)
c=text_glow(c,(x0+wS+wI+36*SS,y0),'B',fA,BLUE,22*SS)
d=ImageDraw.Draw(c)
fM=ImageFont.truetype(POP,118*SS)
spaced(d,x0+6*SS,620*SS,'STUDIO MIXER',fM,(236,236,242,255),14*SS)
# divider red->blue
for i in range(1300*SS):
    t=i/(1300*SS); col=tuple(int(RED[j]*(1-t)+BLUE[j]*t) for j in range(3))
    d.line([x0+8*SS+i,775*SS,x0+8*SS+i,781*SS],fill=col+(255,))
fT=ImageFont.truetype(POPM,54*SS)
spaced(d,x0+10*SS,805*SS,'SHADOWS IN THE BOOTH',fT,(150,150,168,255),16*SS)
c.resize((2400,1000),Image.LANCZOS).convert('RGB').save('/home/claude/logo/SIB_Studio_Mixer_logo.png')

# ---------- square app logo 1024 ----------
S=1024*SS
q=Image.new('RGBA',(S,S),BG+(255,))
em=emblem(760*SS); q.alpha_composite(em,((S-760*SS)//2,20*SS))
d=ImageDraw.Draw(q)
fA2=ImageFont.truetype(ANTON,150*SS)
tw=d.textlength('SIB',font=fA2)+2*8*SS
x=(S-tw)/2; y=760*SS
wS=d.textlength('S',font=fA2); wI=d.textlength('I',font=fA2)
q=text_glow(q,(x,y),'S',fA2,RED,10*SS); d=ImageDraw.Draw(q)
q=text_glow(q,(x+wS+8*SS,y),'I',fA2,(236,236,242),2*SS); d=ImageDraw.Draw(q)
q=text_glow(q,(x+wS+wI+16*SS,y),'B',fA2,BLUE,10*SS); d=ImageDraw.Draw(q)
f3=ImageFont.truetype(POP,40*SS); w3=spaced_w(d,'STUDIO MIXER',f3,10*SS)
spaced(d,(S-w3)/2,955*SS,'STUDIO MIXER',f3,(200,200,214,255),10*SS)
q=q.resize((1024,1024),Image.LANCZOS)
q.convert('RGB').save('/home/claude/logo/SIB_Studio_Mixer_icon.png')
# transparent emblem for app icon
e=emblem(512*SS).resize((512,512),Image.LANCZOS); e.save('/home/claude/logo/emblem.png')
print('done')
