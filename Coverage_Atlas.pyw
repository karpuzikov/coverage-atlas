from __future__ import annotations
import os, sys, re, json, math, hashlib, threading, subprocess, importlib.util, time, traceback, platform
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict, Counter

APP='Coverage Atlas'; VERSION='0.1.1'; STATUS='Under construction ⚠️'

def docs_dir():
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            p=ctypes.c_wchar_p(); ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ctypes.c_byte.from_buffer_copy(bytes.fromhex('FDD39AD0238F46AFADB46C85480369C7'))),0,None,ctypes.byref(p))
        except Exception: pass
    return Path.home()/'Documents'
DATA=docs_dir()/'Karpuzikov Tools'/APP
for d in ('logs','cache','temp'): (DATA/d).mkdir(parents=True,exist_ok=True)
SETTINGS=DATA/'settings.json'

class Diagnostics:
    def __init__(self):
        self.lock=threading.RLock()
        stamp=time.strftime('%Y-%m-%d-%H-%M-%S')
        self.path=DATA/'logs'/f'{APP} Analysis {stamp}.jsonl'
        self._fh=None
        try:
            self._fh=self.path.open('a',encoding='utf-8',buffering=1)
        except Exception:
            self._fh=None
        self.event('app_start', python=sys.version.split()[0], platform=platform.platform(), executable=sys.executable)
    def event(self,event,**data):
        if not self._fh: return
        row={'ts':time.strftime('%Y-%m-%dT%H:%M:%S'), 'event':event, 'app':APP, 'version':VERSION}
        row.update(data)
        try:
            line=json.dumps(row,ensure_ascii=False,separators=(',',':'),default=str)
            with self.lock:
                self._fh.write(line+'\n'); self._fh.flush()
        except Exception:
            pass
    def error(self,event,exc,**data):
        data.update(error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc())
        self.event(event,**data)
    def close(self):
        try:
            self.event('app_exit')
            if self._fh: self._fh.close()
        except Exception:
            pass

LOG=Diagnostics()

def ensure_mutagen():
    try: import mutagen; return
    except ImportError: pass
    cmd=[sys.executable,'-m','pip','install','--user','mutagen>=1.47,<2']
    subprocess.check_call(cmd, creationflags=(0x08000000 if os.name=='nt' else 0))
ensure_mutagen()
from mutagen import File as MFile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

AUDIO={'.flac','.mp3','.m4a','.mp4','.aac','.ogg','.opus','.wav','.wma','.ape','.wv','.tta','.mka','.dsf','.dff'}

def norm(s):
    s=(s or '').casefold().replace('’',"'")
    s=re.sub(r'\s+',' ',s).strip()
    return re.sub(r'[^\w\s\'\-\(\)\[\]]+','',s)

def first(tags,*keys):
    for k in keys:
        try:
            v=tags.get(k)
            if v:
                if isinstance(v,list): v=v[0]
                if hasattr(v,'text'): v=v.text[0] if v.text else ''
                return str(v)
        except Exception: pass
    return ''

def vals(tags,*keys):
    out=[]
    for k in keys:
        try:
            v=tags.get(k)
            if not v: continue
            if hasattr(v,'text'): v=v.text
            if not isinstance(v,(list,tuple)): v=[v]
            out += [str(x) for x in v if x]
        except Exception: pass
    return out

def tag(tags,name):
    aliases={
      'title':('title','TIT2','©nam'),'artist':('artist','TPE1','©ART'),'album':('album','TALB','©alb'),
      'albumartist':('albumartist','album artist','TPE2','aART'),'date':('date','year','TDRC','©day'),
      'barcode':('barcode','BARCODE','----:com.apple.iTunes:BARCODE'),
      'isrc':('isrc','TSRC','----:com.apple.iTunes:ISRC'),
      'mbrec':('musicbrainz_recordingid','MUSICBRAINZ_TRACKID','UFID:http://musicbrainz.org'),
      'mbrel':('musicbrainz_albumid','MUSICBRAINZ_ALBUMID'),
      'disc':('discnumber','TPOS','disk'),'track':('tracknumber','TRCK','trkn')}
    return first(tags,*aliases[name])

def version_hint(title):
    m=re.findall(r'[\(\[]([^\)\]]*(?:mix|remix|edit|version|live|acoustic|instrumental|radio|extended|dub|demo|remaster|karaoke)[^\)\]]*)[\)\]]',title or '',re.I)
    return norm(' '.join(m))

@dataclass
class Track:
    path:str; title:str; artist:str; duration:float; key:str; release:str; trackno:str=''; isrc:str=''; mbrec:str=''
@dataclass
class Release:
    rid:str; folder:str; name:str; date:str=''; barcode:str=''; albumartist:str=''; tracks:list[Track]=field(default_factory=list)
    keys:set[str]=field(default_factory=set)

class Model:
    def __init__(self): self.releases={}; self.forced=set(); self.excluded=set(); self.kept=set(); self.cover=defaultdict(set); self.reason={}
    def scan(self,root,progress=lambda a,b:None):
        started=time.perf_counter(); rootp=Path(root)
        files=[p for p in rootp.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO]
        LOG.event('scan_start',file_count=len(files),root_name=rootp.name)
        groups={}; failures=0; identity_counts=Counter()
        for i,p in enumerate(files,1):
            progress(i,len(files))
            rel=str(p.relative_to(rootp)) if p.is_relative_to(rootp) else p.name
            try:
                f=MFile(p,easy=False); tags=f.tags or {}; dur=float(getattr(getattr(f,'info',None),'length',0) or 0)
                title=tag(tags,'title') or p.stem; artist=tag(tags,'artist'); album=tag(tags,'album') or p.parent.name
                aa=tag(tags,'albumartist'); date=tag(tags,'date'); barcode=tag(tags,'barcode'); mbrel=tag(tags,'mbrel')
                disc=tag(tags,'disc'); trno=tag(tags,'track'); isrc=tag(tags,'isrc'); mbrec=tag(tags,'mbrec')
                folder=str(p.parent)
                rid=mbrel or hashlib.sha1((folder+'|'+album+'|'+barcode).encode('utf8','ignore')).hexdigest()[:16]
                if rid not in groups: groups[rid]=Release(rid,folder,album,date,barcode,aa)
                bucket=round(dur/2)*2 if dur else 0
                hint=version_hint(title)
                if mbrec:
                    source='musicbrainz_recording_id'; key='mb:'+norm(mbrec)
                elif isrc:
                    source='isrc'; key='isrc:'+norm(isrc)
                else:
                    source='metadata'; key='meta:'+hashlib.sha1(f'{norm(title)}|{norm(artist)}|{hint}|{bucket}'.encode()).hexdigest()[:20]
                identity_counts[source]+=1
                groups[rid].tracks.append(Track(str(p),title,artist,dur,key,rid,trno,isrc,mbrec)); groups[rid].keys.add(key)
                LOG.event('track_identity',relative_path=rel,release_id=rid,release=album,title=title,artist=artist,
                          duration_sec=round(dur,3),duration_bucket=bucket,version_hint=hint,identity_source=source,
                          identity_key=key,isrc=isrc or '',musicbrainz_recording_id=mbrec or '',disc=disc or '',track=trno or '')
            except Exception as exc:
                failures+=1; LOG.error('file_read_error',exc,relative_path=rel)
        self.releases=groups
        for rid,r in sorted(groups.items()):
            try: relfolder=str(Path(r.folder).relative_to(rootp))
            except Exception: relfolder=Path(r.folder).name
            LOG.event('release_summary',release_id=rid,release=r.name,date=r.date,barcode=r.barcode,albumartist=r.albumartist,
                      relative_folder=relfolder,track_count=len(r.tracks),distinct_identity_count=len(r.keys))
        LOG.event('scan_parsed',release_count=len(groups),file_failures=failures,identity_sources=dict(identity_counts),elapsed_sec=round(time.perf_counter()-started,4))
        self.rebuild(); self.optimize()
        LOG.event('scan_complete',release_count=len(self.releases),kept_count=len(self.kept),elapsed_sec=round(time.perf_counter()-started,4))
    def rebuild(self):
        self.cover=defaultdict(set)
        for r in self.releases.values():
            for k in r.keys: self.cover[k].add(r.rid)
        distribution=Counter(len(v) for v in self.cover.values())
        LOG.event('coverage_rebuilt',identity_count=len(self.cover),provider_count_distribution=dict(sorted(distribution.items())))
        for k,providers in sorted(self.cover.items()):
            LOG.event('coverage_group',identity_key=k,provider_release_ids=sorted(providers),provider_count=len(providers))
    def optimize(self):
        started=time.perf_counter()
        avail=set(self.releases)-self.excluded
        forced=self.forced & avail
        universe=set().union(*(self.releases[r].keys for r in avail)) if avail else set()
        covered=set().union(*(self.releases[r].keys for r in forced)) if forced else set()
        chosen=set(forced); remaining=universe-covered
        LOG.event('optimize_start',release_count=len(self.releases),available_count=len(avail),forced=sorted(forced),
                  excluded=sorted(self.excluded),identity_universe=len(universe),already_covered=len(covered))
        greedy_step=0
        while remaining:
            candidates=[r for r in avail-chosen if self.releases[r].keys & remaining]
            if not candidates:
                LOG.event('optimizer_uncovered',remaining_identity_keys=sorted(remaining)); break
            scored=[]
            for r in candidates:
                scored.append({'release_id':r,'new_coverage':len(self.releases[r].keys & remaining),
                               'release_identity_count':len(self.releases[r].keys),'date':self.releases[r].date,'name':self.releases[r].name})
            scored.sort(key=lambda z:(z['new_coverage'],-z['release_identity_count'],z['date'],z['name']),reverse=True)
            r=max(candidates,key=lambda x:(len(self.releases[x].keys & remaining),-len(self.releases[x].keys),self.releases[x].date,self.releases[x].name))
            new_keys=self.releases[r].keys & remaining; chosen.add(r); remaining-=self.releases[r].keys; greedy_step+=1
            LOG.event('greedy_choice',step=greedy_step,chosen_release_id=r,chosen_release=self.releases[r].name,
                      newly_covered=len(new_keys),remaining_after=len(remaining),candidate_scores=scored)
        changed=True; removed=[]
        while changed:
            changed=False
            for r in sorted(chosen-forced,key=lambda x:(len(self.releases[x].keys),self.releases[x].name)):
                others=chosen-{r}; cov=set().union(*(self.releases[x].keys for x in others)) if others else set()
                if universe<=cov:
                    chosen.remove(r); removed.append(r); changed=True
                    LOG.event('redundancy_elimination',removed_release_id=r,removed_release=self.releases[r].name,remaining_selected=len(chosen))
        optional=sorted(avail-forced)
        best=set(chosen); exact_stats={'nodes':0,'pruned_size':0,'solutions':0,'improvements':0}
        if len(optional)<=70:
            basecov=set().union(*(self.releases[r].keys for r in forced)) if forced else set()
            LOG.event('exact_search_start',optional_count=len(optional),seed_size=len(best),base_coverage=len(basecov))
            def dfs(sel,cov):
                nonlocal best
                exact_stats['nodes']+=1
                if len(sel)>=len(best): exact_stats['pruned_size']+=1; return
                miss=universe-cov
                if not miss:
                    exact_stats['solutions']+=1; best=set(sel); exact_stats['improvements']+=1
                    LOG.event('exact_best_improved',selected_count=len(best),selected_release_ids=sorted(best)); return
                k=min(miss,key=lambda q:len((self.cover[q]&avail)-sel))
                opts=sorted((self.cover[k]&avail)-sel,key=lambda r:(-len(self.releases[r].keys & miss),self.releases[r].name))
                for r in opts: dfs(sel|{r},cov|self.releases[r].keys)
            dfs(set(forced),basecov)
            LOG.event('exact_search_complete',best_size=len(best),**exact_stats)
        else:
            LOG.event('exact_search_skipped',optional_count=len(optional),limit=70,reason='candidate_count_above_limit')
        self.kept=best
        self.reason={}
        kc=Counter(k for r in self.kept for k in self.releases[r].keys)
        for rid,r in self.releases.items():
            if rid in self.excluded: self.reason[rid]='Excluded by user'
            elif rid in self.kept:
                unique=[k for k in r.keys if kc[k]==1]
                self.reason[rid]=f'Kept: supplies {len(unique)} required track version(s) not supplied by another kept release' if unique else 'Kept: part of the smallest coverage plan found'
            elif not r.keys: self.reason[rid]='Irrelevant: contributes no analyzable tracks'
            else:
                self.reason[rid]='Redundant in current plan: all track versions are supplied by kept releases'
        status_counts=Counter(('EXCLUDED' if rid in self.excluded else 'FORCED' if rid in self.forced else 'KEEP' if rid in self.kept else 'IRRELEVANT' if not r.keys else 'REDUNDANT') for rid,r in self.releases.items())
        LOG.event('optimize_complete',kept_release_ids=sorted(self.kept),kept_count=len(self.kept),status_counts=dict(status_counts),
                  elapsed_sec=round(time.perf_counter()-started,4),reasons={rid:self.reason[rid] for rid in sorted(self.reason)})
    def impact(self,rid):
        if rid not in self.kept: return []
        others=self.kept-{rid}; cov=set().union(*(self.releases[x].keys for x in others)) if others else set()
        return sorted(self.releases[rid].keys-cov)

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.m=Model(); LOG.event('ui_start'); self.title(f'{APP} {VERSION} - {STATUS}'); self.geometry('1420x860'); self.minsize(1050,650)
        self.configure(bg='#16191d'); self.style(); self.build(); self.load_settings(); self.protocol('WM_DELETE_WINDOW',self.on_close)
    def style(self):
        s=ttk.Style(self); s.theme_use('clam');
        s.configure('.',background='#1d2126',foreground='#e7e9ec',fieldbackground='#242a30',font=('Segoe UI',10)); s.configure('TFrame',background='#16191d'); s.configure('TLabel',background='#16191d'); s.configure('TButton',padding=7); s.configure('Treeview',rowheight=27,background='#1d2126',fieldbackground='#1d2126',foreground='#e7e9ec'); s.configure('Treeview.Heading',background='#2a3037',foreground='#fff',font=('Segoe UI Semibold',10)); s.map('Treeview',background=[('selected','#34495e')])
    def build(self):
        top=ttk.Frame(self); top.pack(fill='x',padx=12,pady=10)
        ttk.Label(top,text=APP,font=('Segoe UI Semibold',18)).pack(side='left'); ttk.Label(top,text=STATUS).pack(side='left',padx=12)
        ttk.Button(top,text='Analyze folder',command=self.choose).pack(side='right'); ttk.Button(top,text='Reset decisions',command=self.reset).pack(side='right',padx=6); ttk.Button(top,text='Open logs',command=self.open_logs).pack(side='right')
        self.q=tk.StringVar(); e=ttk.Entry(top,textvariable=self.q,width=34); e.pack(side='right',padx=8); e.bind('<KeyRelease>',lambda _:self.refresh())
        pan=ttk.Panedwindow(self,orient='horizontal'); pan.pack(fill='both',expand=True,padx=12,pady=(0,12))
        left=ttk.Frame(pan); right=ttk.Frame(pan); pan.add(left,weight=3); pan.add(right,weight=2)
        cols=('status','date','release','tracks','unique','barcode')
        self.tree=ttk.Treeview(left,columns=cols,show='headings');
        for c,w in [('status',100),('date',95),('release',390),('tracks',65),('unique',70),('barcode',135)]: self.tree.heading(c,text=c.title()); self.tree.column(c,width=w,anchor='w')
        self.tree.pack(fill='both',expand=True); self.tree.bind('<<TreeviewSelect>>',self.select)
        bar=ttk.Frame(left); bar.pack(fill='x',pady=(8,0));
        ttk.Button(bar,text='Exclude release',command=self.exclude).pack(side='left'); ttk.Button(bar,text='Force keep',command=self.force).pack(side='left',padx=6); ttk.Button(bar,text='Clear decision',command=self.clear_decision).pack(side='left')
        self.summary=ttk.Label(bar,text=''); self.summary.pack(side='right')
        self.detail=tk.Text(right,bg='#111418',fg='#e7e9ec',insertbackground='white',relief='flat',font=('Consolas',10),wrap='word',padx=12,pady=12); self.detail.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(right,height=240,bg='#111418',highlightthickness=0); self.canvas.pack(fill='x',pady=(8,0)); self.canvas.bind('<Button-1>',self.canvas_click); self.nodes=[]
        self.status=ttk.Label(self,text='Ready'); self.status.pack(fill='x',padx=12,pady=(0,8))
    def choose(self):
        p=filedialog.askdirectory(title='Select music collection');
        if not p:return
        self.rootpath=p; LOG.event('user_analyze_folder',root_name=Path(p).name); self.status.config(text='Scanning...')
        def run():
            self.m.scan(p,lambda i,n:self.after(0,lambda:self.status.config(text=f'Scanning {i:,}/{n:,} files...'))); self.after(0,self.done)
        threading.Thread(target=run,daemon=True).start()
    def done(self): self.refresh(); self.status.config(text=f'Analysis complete - {len(self.m.releases):,} releases - log: {LOG.path.name}'); self.save_settings(); LOG.event('ui_analysis_complete',release_count=len(self.m.releases),kept_count=len(self.m.kept))
    def status_of(self,r):
        if r in self.m.excluded:return 'EXCLUDED'
        if r in self.m.forced:return 'FORCED'
        if r in self.m.kept:return 'KEEP'
        if not self.m.releases[r].keys:return 'IRRELEVANT'
        return 'REDUNDANT'
    def refresh(self):
        sel=self.tree.selection(); old=sel[0] if sel else None
        self.tree.delete(*self.tree.get_children()); q=norm(self.q.get())
        kc=Counter(k for r in self.m.kept for k in self.m.releases[r].keys)
        for rid,r in sorted(self.m.releases.items(),key=lambda z:(z[1].date,z[1].name)):
            hay=norm(r.name+' '+r.barcode+' '+' '.join(t.title+' '+t.artist for t in r.tracks))
            if q and q not in hay: continue
            u=sum(1 for k in r.keys if rid in self.m.kept and kc[k]==1)
            self.tree.insert('', 'end',iid=rid,values=(self.status_of(rid),r.date,r.name,len(r.tracks),u,r.barcode))
        self.summary.config(text=f'{len(self.m.kept)} kept / {len(self.m.releases)} releases')
        if old and self.tree.exists(old): self.tree.selection_set(old); self.select()
    def selected(self):
        s=self.tree.selection(); return s[0] if s else None
    def exclude(self):
        r=self.selected();
        if not r:return
        before=self.m.impact(r)
        if before and not messagebox.askyesno('Recalculate',f'This release currently uniquely supplies {len(before)} track version(s) within the kept plan.\n\nExclude it and recalculate alternatives?'):return
        LOG.event('user_decision',action='exclude',release_id=r,release=self.m.releases[r].name,unique_in_current_plan=len(before)); self.m.excluded.add(r); self.m.forced.discard(r); self.m.optimize(); self.refresh()
    def force(self):
        r=self.selected();
        if r: LOG.event('user_decision',action='force_keep',release_id=r,release=self.m.releases[r].name); self.m.forced.add(r); self.m.excluded.discard(r); self.m.optimize(); self.refresh()
    def clear_decision(self):
        r=self.selected();
        if r: LOG.event('user_decision',action='clear',release_id=r,release=self.m.releases[r].name); self.m.forced.discard(r); self.m.excluded.discard(r); self.m.optimize(); self.refresh()
    def reset(self): LOG.event('user_decision',action='reset_all'); self.m.forced.clear(); self.m.excluded.clear(); self.m.optimize(); self.refresh()
    def select(self,*_):
        rid=self.selected();
        if not rid:return
        r=self.m.releases[rid]; impact=set(self.m.impact(rid)); self.detail.delete('1.0','end')
        self.detail.insert('end',f'{r.date} - {r.name}\n{r.albumartist}\nBarcode: {r.barcode or "-"}\nFolder: {r.folder}\n\n{self.status_of(rid)}\n{self.m.reason.get(rid,"")}\n\nTRACKS\n')
        for i,t in enumerate(r.tracks,1):
            flag='  [IRREPLACEABLE IN CURRENT PLAN]' if t.key in impact else ''
            alt=len(self.m.cover[t.key]-{rid}); self.detail.insert('end',f'{i:02d}. {t.artist} - {t.title}  ({t.duration:.0f}s)  alternatives:{alt}{flag}\n')
        self.draw_graph(rid)
    def draw_graph(self,rid):
        c=self.canvas; c.delete('all'); self.nodes=[]; w=max(c.winfo_width(),500); h=240; cx=w/2; cy=h/2
        r=self.m.releases[rid]; related=Counter()
        for k in r.keys:
            for x in self.m.cover[k]-{rid}: related[x]+=1
        rel=related.most_common(10)
        c.create_oval(cx-55,cy-28,cx+55,cy+28,fill='#34495e',outline=''); c.create_text(cx,cy,text=r.name[:18],fill='white',width=100)
        for j,(x,n) in enumerate(rel):
            a=2*math.pi*j/max(1,len(rel)); x0=cx+180*math.cos(a); y0=cy+82*math.sin(a)
            c.create_line(cx,cy,x0,y0,fill='#59636e',width=max(1,min(6,n)))
            c.create_oval(x0-48,y0-22,x0+48,y0+22,fill='#242a30',outline='#59636e'); c.create_text(x0,y0,text=self.m.releases[x].name[:15],fill='#e7e9ec',width=90)
            self.nodes.append((x0-48,y0-22,x0+48,y0+22,x))
    def canvas_click(self,e):
        for x1,y1,x2,y2,r in self.nodes:
            if x1<=e.x<=x2 and y1<=e.y<=y2 and self.tree.exists(r): self.tree.selection_set(r); self.tree.see(r); self.select(); break
    def open_logs(self):
        try:
            folder=str(DATA/'logs')
            if os.name=='nt': os.startfile(folder)
            elif sys.platform=='darwin': subprocess.Popen(['open',folder])
            else: subprocess.Popen(['xdg-open',folder])
            LOG.event('open_logs_folder')
        except Exception as exc:
            LOG.error('open_logs_error',exc); messagebox.showerror(APP,f'Could not open logs folder:\n{exc}')
    def report_callback_exception(self,exc,val,tb):
        LOG.event('ui_callback_error',error_type=getattr(exc,'__name__',str(exc)),error=str(val),traceback=''.join(traceback.format_exception(exc,val,tb)))
        messagebox.showerror(APP,f'Unexpected error: {val}\n\nThe diagnostic log contains the traceback.')
    def on_close(self):
        self.save_settings(); LOG.close(); self.destroy()
    def load_settings(self):
        try:
            d=json.loads(SETTINGS.read_text('utf8')); self.last=d.get('last_folder','')
        except Exception:self.last=''
    def save_settings(self):
        try: SETTINGS.write_text(json.dumps({'last_folder':getattr(self,'rootpath','')},indent=2),'utf8')
        except Exception:pass

def _fatal_hook(exc_type,exc,tb):
    LOG.event('fatal_error',error_type=getattr(exc_type,'__name__',str(exc_type)),error=str(exc),traceback=''.join(traceback.format_exception(exc_type,exc,tb)))
    sys.__excepthook__(exc_type,exc,tb)
sys.excepthook=_fatal_hook
if hasattr(threading,'excepthook'):
    def _thread_hook(args):
        LOG.event('thread_error',thread=getattr(args.thread,'name',''),error_type=getattr(args.exc_type,'__name__',str(args.exc_type)),error=str(args.exc_value),traceback=''.join(traceback.format_exception(args.exc_type,args.exc_value,args.exc_traceback)))
    threading.excepthook=_thread_hook

if __name__=='__main__': App().mainloop()
