"""Local neural OCR observations; visual evidence and semantic parsing stay separate."""
import hashlib
import json
import marshal
import re
from pathlib import Path
from .gameplay import PANE,CURRENCIES,classify,effects_from_lines,preview_effects
from .reconcile import FIELDS
from .stats import BOXES

PARAMS = {'Global.use_cls':False,'Det.limit_side_len':736,'Det.limit_type':'max',
          'EngineConfig.onnxruntime.intra_op_num_threads':2,
          'EngineConfig.onnxruntime.inter_op_num_threads':1,'Global.log_level':'warning'}


def within(line,box):
    left,top,right,bottom=line['box']
    return box[0] <= (left+right)/2 <= box[2] and box[1] <= (top+bottom)/2 <= box[3]


def number(observation,minimum=97):
    text=observation.get('text','').strip()
    return int(text) if re.fullmatch(r'\d{1,4}',text) and observation['confidence']>=minimum else None


def training_preview(raw):
    """Recognize the failure popup above any selected training tab.

    Its percentage can be occluded or mid-animation. The fixed label, training
    header and current-stat layout establish a preview, not an awarded gain.
    """
    if not raw['current_grid'] or raw['header'].strip().lower()!='training':return False
    for line in raw['lines']:
        if line['text'].strip().lower()!='failure' or line['confidence']<90:continue
        left,top,right,bottom=line['box']
        if (250<=left<right<=850 and 750<=top<bottom<=850
            and right-left<=110 and bottom-top<=45):return True
    return False


class NeuralReader:
    def __init__(self,model_dir='.local/models/rapidocr'):
        from rapidocr import RapidOCR,OCRVersion,ModelType,LangRec
        import numpy as np
        from PIL import Image
        from rapidocr.ch_ppocr_rec import TextRecInput
        self.np,self.Image,self.TextRecInput=np,Image,TextRecInput
        self.engine=RapidOCR(params=dict(PARAMS,**{'Global.model_root_dir':str(model_dir),
            'Rec.lang_type':LangRec.EN,'Rec.ocr_version':OCRVersion.PPOCRV5,'Rec.model_type':ModelType.MOBILE}))
        self.models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(model_dir).glob('*.onnx') if p.name in ('PP-OCRv6_det_small.onnx','en_PP-OCRv5_rec_mobile.onnx')}
        self.fingerprint=hashlib.sha256(json.dumps([PARAMS,self.models],sort_keys=True).encode()+marshal.dumps(type(self).read.__code__)+b'rapidocr3.9.2-en-v5-v1').hexdigest()

    def read_training(self,pane):
        """High-rate result inspection without running the general text detector."""
        if pane.size!=(810,1080):raise ValueError('Expected the gameplay crop.')
        array=self.np.array(pane.convert('RGB'))
        def crop(b):return array[b[1]:b[3],b[0]-148:b[2]-148,::-1]
        requests=[('header',(155,0,250,29)),('option',(220,162,400,198))]
        blue=[]
        for x in (270,470):
            c=crop((x,911,x+35,934)).astype('int16')
            blue.append(float(((c[:,:,0]>c[:,:,2]+25)&(c[:,:,1]>c[:,:,2]+15)&(c[:,:,0]>100)).mean()))
        grid=min(blue)>.45
        boxes=[(300,832,414,890),(498,832,610,890),(696,832,812,890),(300,950,414,1008),(498,950,610,1008),(696,950,812,1008)]
        requests += [('gain.'+f,b) for f,b in zip(FIELDS,boxes)]
        requests += [('wide_gain.'+f,((250,448,646)[i%3],812 if i<3 else 930,(462,660,858)[i%3],909 if i<3 else 1027)) for i,f in enumerate(FIELDS)]
        requests += [('result.'+f,((322,518,714)[i%3],834 if i<3 else 952,(322,518,714)[i%3]+126,876 if i<3 else 994)) for i,f in enumerate(FIELDS)]
        requests += [('result.skill_points',(708,952,810,990))]
        result=self.engine.text_rec(self.TextRecInput(img=[crop(b) for _,b in requests]))
        regions={name:dict(text=text,confidence=round(float(score)*100,4),box=list(box)) for (name,box),text,score in zip(requests,result.txts,result.scores)}
        header=regions['header']['text'] if regions['header']['confidence']>=95 else ''
        totals=sum(bool(re.fullmatch(r'\d{1,4}/\d{3,4}',regions['result.'+f]['text'])) and regions['result.'+f]['confidence']>=95 for f in FIELDS[:5])
        return dict(lines=[regions['header'],regions['option']],regions=regions,header=header,
            result_grid=(grid or totals>=3) and header.lower().startswith('training'),current_grid=False,
            engine_fingerprint=self.fingerprint+'-training-'+hashlib.sha256(marshal.dumps(type(self).read_training.__code__)).hexdigest(),
            model_sha256=self.models,gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),inspection='training_result_only')

    def read(self,pane):
        if pane.size != (810,1080):
            raise ValueError('Neural OCR accepts only the gameplay crop.')
        array=self.np.array(pane.convert('RGB'))
        result=self.engine(array[:,:,::-1])
        lines=[]
        if result.txts:
            for box,text,score in zip(result.boxes,result.txts,result.scores):
                lines.append(dict(text=text,confidence=round(float(score)*100,4),
                    box=[round(float(box[:,0].min()))+148,round(float(box[:,1].min())),round(float(box[:,0].max()))+148,round(float(box[:,1].max()))]))
        def crop(box):
            return array[box[1]:box[3],box[0]-148:box[2]-148,::-1]
        def blue(box):
            colors=crop(box).astype('int16')
            return float(((colors[:,:,0]>colors[:,:,2]+25)&(colors[:,:,1]>colors[:,:,2]+15)&(colors[:,:,0]>100)).mean())
        header=' '.join(l['text'] for l in lines if within(l,(148,0,450,30)) and l['confidence']>=90)
        text='\n'.join(l['text'] for l in lines)
        grid=min(blue((x,911,x+35,934)) for x in (270,470))>.45 and header.lower().startswith('training')
        current=min(blue((x,700,x+35,719)) for x in (310,410,510,610,710))>.35
        requests=[]
        if current:
            requests += [(f'current.{field}',box) for field,box in zip(FIELDS,BOXES)]
            requests += [('countdown',(264,57,325,101))]
        if grid:
            gain_boxes=[(300,832,414,890),(498,832,610,890),(696,832,812,890),(300,950,414,1008),(498,950,610,1008),(696,950,812,1008)]
            requests += [(f'gain.{f}',b) for f,b in zip(FIELDS,gain_boxes)]
            requests += [(f'result.{f}',((322,518,714)[i%3],834 if i<3 else 952,(322,518,714)[i%3]+126,876 if i<3 else 994)) for i,f in enumerate(FIELDS)]
            requests += [('result.skill_points',(708,952,810,990))]
        if 'spend performance points to learn' in text.lower():
            requests += [(f'projected_performance.{f}',(392+83*i,846,425+83*i,876)) for i,f in enumerate(CURRENCIES)]
            boxes=[(308,376,365,404),(403,376,455,404),(497,376,549,404),(591,376,644,404),(683,376,735,404),(756,376,817,404)]
            requests += [(f'modal_current.{f}',b) for f,b in zip(FIELDS,boxes)]
        elif header.lower().startswith('lessons'):
            requests += [(f'performance.{f}',(344+104*i,88,394+104*i,123)) for i,f in enumerate(CURRENCIES)]
        regions={}
        if requests:
            rec=self.engine.text_rec(self.TextRecInput(img=[crop(b) for _,b in requests]))
            for (name,box),text,score in zip(requests,rec.txts,rec.scores):
                regions[name]=dict(text=text,confidence=round(float(score)*100,4),box=list(box))
        return dict(lines=lines,regions=regions,header=header,result_grid=grid,current_grid=current,
                    engine_fingerprint=self.fingerprint,model_sha256=self.models,
                    gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())


def performance_panel_facts(lines, screen, stats):
    """Read the shared sidebar; only training results can award its gain badges."""
    eligible = screen in ('training_preview', 'training_result') or (
        stats.get('observation_profile') == 'race_day_lower_totals'
        and all(type(stats.get('values', {}).get(f)) is int for f in FIELDS))
    if not eligible or not any(l['text']=='Performance' and within(l,(150,250,270,285)) for l in lines):
        return {}
    points={};projected={}
    for i,field in enumerate(CURRENCIES):
        candidates=[l for l in lines if l['confidence']>=97 and within(l,(200,290+56*i,330,330+56*i))]
        matches=[re.fullmatch(r'(\d{1,3})(?:\+(\d{1,3}))?',l['text']) for l in candidates]
        matches=[m for m in matches if m]
        if len(matches)==1:
            points[field]=int(matches[0][1])
            if matches[0][2]:projected[field]=int(matches[0][2])
        separate=[re.fullmatch(r'\+(\d{1,3})',l['text']) for l in candidates]
        separate={int(m[1]) for m in separate if m}
        if field not in projected and len(separate)==1:projected[field]=separate.pop()
    result={'performance_points':points}
    if screen=='training_preview':result['projected_performance_gains']=projected
    elif screen=='training_result':result['awarded_performance_gains']=projected
    return result


def parse(raw):
    lines=raw['lines'];regions=raw['regions'];text='\n'.join(l['text'] for l in lines if l['confidence']>=90)
    header=raw['header']
    if raw.get('inspection')=='training_result_only' and not header:
        observed=regions.get('header',{})
        if observed.get('text','').strip().lower()=='training' and observed.get('confidence',0)>=85:
            header='Training'
    def values(prefix,fields=FIELDS):
        return {f:number(regions.get(prefix+'.'+f,{})) for f in fields}
    def currencies(modal=False):
        # Full detector boxes preserve leading digits that tight fixed crops can cut off.
        result={}
        for i,field in enumerate(CURRENCIES):
            box=(382+83*i,840,436+83*i,883) if modal else (337+104*i,85,407+104*i,126)
            candidates=[l for l in lines if within(l,box)]
            # A detector can merge adjacent counters during the modal fade.
            # Such a box is not an observation of either individual currency.
            numbers=[number(l) for l in candidates if number(l) is not None
                     and l['box'][2]-l['box'][0]<=(75 if modal else 100) and number(l)<=999]
            result[field]=numbers[0] if len(numbers)==1 else None
            wide=regions.get(('wide_projected_performance.' if modal else 'wide_performance.')+field,{})
            value=number(wide)
            if value is not None and value<=999:
                # Wider crops are purpose-built to include the full counter;
                # tight legacy numeric regions are deliberately not consulted.
                result[field]=value
            padding=raw.get('currency_padding',{}).get(field) if not modal else None
            if padding is not None:
                from .refine_skill_points import counter_reading
                value,conflicts=counter_reading(padding,result[field])
                result[field]=value if not conflicts and type(value) is int and 0<=value<=999 else None
        return result
    preview=training_preview(raw)
    result_grid=raw['result_grid'] and not preview
    if raw.get('inspection')=='training_result_only' and header.lower().startswith('training'):
        count=sum(bool(re.fullmatch(r'\d{1,4}/\d{4}',regions.get('result.'+f,{}).get('text',''))) and regions.get('result.'+f,{}).get('confidence',0)>=90 for f in FIELDS[:5])
        result_grid=result_grid or count>=2
    screen=classify(text,header,result_grid,preview)
    if 'Go on a fun outing?' in text and 'entire turn' in text:screen='outing_confirmation'
    elif 'Recreation' in text and 'Event Progress' in text and 'Trainee Umamusume' in text:screen='outing_selection'
    if 'Concert bonuses updated!' in text:screen='concert_bonus_update'
    elif 'Concert Info' in text and 'Concert Bonus Changes' in text:screen='concert_info'
    if screen=='unknown' and raw['header'].lower().startswith('complete career') and 'remaining performance points' in text.lower():screen='career_completion_hub'
    if screen=='unknown' and raw['header'].lower().startswith('training') and not raw['current_grid']:
        lower=[l['text'] for l in lines if l['confidence']>=90 and within(l,(260,790,850,1005))]
        labels={t for t in lower if t in ('Speed','Stamina','Power','Guts','Wit','Skill Pts')}
        totals=[t for t in lower if re.search(r'\d{1,4}/\d{3,4}',t)]
        if len(labels)>=2 and len(totals)>=2:screen='training_result_candidate'
    outcome_lines=[l for l in lines if l['confidence']>=95 and within(l,(250,770,850,1000))]
    for first in list(outcome_lines):
        if first['text'].startswith('Learned ') and not re.search(r'[.!]$',first['text']):
            following=[l for l in lines if 90<=l['confidence']<95 and 0<l['box'][1]-first['box'][1]<40 and re.fullmatch(r'Class[.!]',l['text'])]
            outcome_lines.extend(following)
    outcome_lines.sort(key=lambda l:(l['box'][1],l['box'][0]))
    joined=[];index=0
    while index<len(outcome_lines):
        line=outcome_lines[index]
        if re.match(r'Learned the song ["“]',line['text']) and not re.search(r'["”][.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if 0<following['box'][1]-line['box'][1]<40 and re.fullmatch(r'[^"“”]+["”][.!]',following['text']):
                original_prefix=line.get('original_symbol_text',line['text'])
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
                # A symbol on the continuation belongs to the joined receipt.
                # Keep its physical source lines in the observation and the
                # complete original sentence alongside the normalized effect.
                if following.get('visual_symbol_observation') and isinstance(following.get('original_symbol_text'),str):
                    line.update(original_symbol_text=original_prefix+' '+following['original_symbol_text'],
                                visual_symbol_observation=following['visual_symbol_observation'])
        elif line['text'].startswith('Learned ') and not re.search(r'[.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if (0<following['box'][1]-line['box'][1]<40 and abs(following['box'][0]-line['box'][0])<=15
                and len(following['text'].split())<=4 and re.fullmatch(r'[A-Z][^.!?]{0,60}[.!]',following['text'])
                and not effects_from_lines([following])):
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
        elif re.match(r'Gained \d+ hint level\(?s?\)? for ',line['text']) and not re.search(r'[.!]$',line['text']) and index+1<len(outcome_lines):
            following=outcome_lines[index+1]
            if (0<following['box'][1]-line['box'][1]<40 and abs(following['box'][0]-line['box'][0])<=15
                and len(following['text'].split())<=4 and re.search(r'[.!]$',following['text']) and not effects_from_lines([following])):
                line=dict(line,text=line['text']+' '+following['text'],confidence=min(line['confidence'],following['confidence']));index+=1
        joined.append(line);index+=1
    repairs={}
    for i,line in enumerate(joined):
        # The optional plural marker is fixed UI grammar. Missing marker
        # characters cannot supply a missing digit or change the skill name.
        fixed=re.sub(r'^(Gained \d+ hint level)\(?s?\)?( for \S.*[.!])$',r'\1(s)\2',line['text'])
        fixed=re.sub(r'^((?:Speed|Stamina|Power|Guts|Wit|Skill Pts)) (?:wet|welt) ((?:up|down) by \d+[.!]?)$',r'\1 went \2',fixed)
        fixed=re.sub(r'^((?:Speed|Stamina|Power|Guts|Wit|Skill Pts|Dance|Passion|Vocals?|Visuals?|Composure) went (?:up|down) by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r'^(Energy recovered by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r'^(Energy went (?:up|down))by\s*(\d+[.!]?)$',r'\1 by \2',fixed)
        fixed=re.sub(r'^(Energy went (?:up|down) by)(\d+[.!]?)$',r'\1 \2',fixed)
        fixed=re.sub(r"^(Friendship with .+?)(?<!\s)(didn't go up[.!]?)$",r'\1 \2',fixed)
        fixed=re.sub(r'^(Friendship with .+? is)maxed out([.!]?)$',r'\1 maxed out\2',fixed)
        # Repair only the fixed receipt keyword. Recipient spelling, amount,
        # confidence and the complete past-tense sentence remain untouched.
        fixed=re.sub(r'^(?:Friewdship|Frendship|Frienlship)( with .+? went up by \d+[.!])$',r'Friendship\1',fixed)
        fixed=re.sub(r'^(Friendship with .+?) (?:wert|ent) (up by \d+[.!])$',r'\1 went \2',fixed)
        fixed=re.sub(r'^Friendship wh (.+? went up by \d+[.!])$',r'Friendship with \1',fixed)
        if fixed!=line['text']:
            repairs[fixed]=line['text'];joined[i]=dict(line,text=fixed)
    parsed_effects=effects_from_lines(joined)
    for effect in parsed_effects:
        symbol_line=next((l for l in joined if l['text']==effect['raw_text'] and l.get('visual_symbol_observation')),None)
        if symbol_line:
            effect.update(original_text=symbol_line['original_symbol_text'],visual_symbol_observation=symbol_line['visual_symbol_observation'])
        if effect['raw_text'] in repairs:
            effect['original_text']=repairs[effect['raw_text']];effect['text_normalization']='fixed_receipt_verb'
    # Typewriter/fade frames can expose a prefix such as "... by 5" of "... by 57."
    # Numeric receipts require their visible sentence terminator in this layout.
    effects=[e for e in parsed_effects if e.get('amount') is None or re.search(r'[.!]$',e['raw_text'])]
    pending_effects=[e for e in parsed_effects if e not in effects]
    if screen not in ('unknown','event_outcome'):
        effects=[];pending_effects=[]
    if (effects or pending_effects) and screen=='unknown':screen='event_outcome'
    options=[]
    for l in lines:
        if within(l,(210,160,420,200)) and l['confidence']>=90:
            m=re.fullmatch(r'(Speed|Stamina|Power|Guts|Wit)\s+Lvl\s*\d+',l['text'],re.I)
            if m:options.append(m[1].lower())
    option=options[0] if len(set(options))==1 and options else None
    stats=dict(values=values('current') if raw['current_grid'] else None,training_preview=preview,
               preview_option=option if preview else None,completed_action=None,
               turns_remaining_to_goal=number(regions.get('countdown',{})))
    if not raw['current_grid']:
        from .race_hub_stats import observation as race_hub_observation
        race_totals=race_hub_observation(lines,grid_verified=raw.get('race_hub_grid_verified') is True)
        if race_totals:
            stats.update(values=race_totals['values'],observation_profile=race_totals['profile'],
                         field_observations=race_totals['field_observations'])
    calendar=[l['text'] for l in lines if within(l,(390,28,830,62)) and l['confidence']>=95
              and re.fullmatch(r'(?:Junior|Classic|Senior) Year (?:Pre-Debut|(?:Early|Late) [A-Z][a-z]{2})|Finale Underway',l['text'])]
    stats['calendar_text']=' '.join(calendar) or None
    facts={}
    if screen=='race_result' and raw.get('race_identity_refinement'):
        facts['race_identity_refinement']=raw['race_identity_refinement']
    achieved=[l for l in lines if l['confidence']>=95
              and within(l,(440,85,760,116)) and l['text'].strip()=='Goal Achieved!']
    if len(achieved)==1:
        facts['goal_status_observation']=dict(status='achieved',raw_text=achieved[0]['text'],
            confidence=achieved[0]['confidence'],box=achieved[0]['box'],
            semantics='visible_status_only; completion_time_and_rewards_not_inferred')
    if raw.get('occluded_receipt_lines'):
        facts['occluded_receipt_lines']=raw['occluded_receipt_lines']
    if raw.get('resolved_receipt_occlusions'):
        facts['resolved_receipt_occlusions']=raw['resolved_receipt_occlusions']
    if raw.get('receipt_overlay_evidence'):
        facts['receipt_overlay_evidence']=raw['receipt_overlay_evidence']
    if screen=='concert_bonus_update':facts['bonus_update_receipt']=True
    if screen=='concert_info':
        current={};planned={};bonus_evidence={}
        for i,field in enumerate(('friendship_training_effectiveness','specialty_priority','support_chain_event_frequency')):
            observations=[l for l in lines if l['confidence']>=90 and within(l,((270,460,650)[i],380,(460,650,840)[i],425))]
            for observation in observations:
                value=re.sub(r'\s+','',observation['text'])
                # The level slot is numeric. Preserve this constrained OCR
                # normalization rather than treating a missing field as zero.
                if i==2:value=re.sub(r'(?<=Lvl)O(?=Lvl|[>▶→]|$)','0',value)
                pair=re.fullmatch(r'\+(\d+)%?[>▶→]?\+(\d+)%?',value) if i<2 else re.fullmatch(r'Lvl(\d+)[>▶→]?Lvl(\d+)',value)
                single=re.fullmatch(r'\+(\d+)%?',value) if i<2 else re.fullmatch(r'Lvl(\d+)',value)
                if pair:current[field]=int(pair[1]);planned[field]=int(pair[2])
                elif single:current[field]=int(single[1]);planned[field]=int(single[1])
                if pair or single:bonus_evidence[field]=dict(observation,normalized_text=value)
        facts.update(current_concert_bonuses=current,planned_concert_bonuses=planned,
                     concert_bonus_evidence=bonus_evidence,
                     bonus_snapshot_is_not_activation=True)
    if pending_effects:facts['effect_candidates']=pending_effects
    from .animated_performance import candidates
    animation=candidates(lines,screen)
    if animation:facts['animated_performance_candidates']=animation
    stat_animation=candidates(lines,screen,stat=True)
    if stat_animation:facts['animated_stat_candidates']=stat_animation
    if screen=='career_summary':
        final={}
        for i,field in enumerate(FIELDS[:5]):
            candidates=[l for l in lines if within(l,(305+113*i,282,382+113*i,324)) and number(l) is not None]
            final[field]=number(candidates[0]) if len(candidates)==1 else None
        facts['final_attributes']=final
        facts['owned_skill_list_complete']=False
        from .inventory import visible_cards
        facts['visible_owned_skill_cards']=visible_cards(raw,final)
    if screen=='career_completion_hub':
        final={}
        for i,field in enumerate(FIELDS[:5]):
            candidates=[l for l in lines if within(l,(718,316+31*i,785,349+31*i)) and number(l) is not None]
            final[field]=number(candidates[0]) if len(candidates)==1 else None
        point_lines=[l for l in lines if l['confidence']>=95 and within(l,(280,900,425,936))]
        matches=[re.fullmatch(r'Skill Pts\s*(\d{1,4})',l['text']) for l in point_lines]
        numbers={int(m[1]) for m in matches if m}
        final['skill_points']=numbers.pop() if len(numbers)==1 else None
        stats['values']=final
        facts['current_skill_points']=final['skill_points']
        facts['final_attributes']=final
    if screen=='career_finish_confirmation':
        # These are remaining balances inside the modal, not spent points or
        # proof that the user finished the career rather than canceling.
        labels=[l for l in lines if l['confidence']>=97 and l['text']=='Remaining Skill Points'
                and within(l,(400,550,590,600))]
        if len(labels)==1:
            matches=[re.fullmatch(r'(\d{1,4})\s*pt\(s\)',l['text']) for l in lines
                     if l['confidence']>=97 and within(l,(585,550,710,600))]
            values={int(m[1]) for m in matches if m}
            facts['current_skill_points']=values.pop() if len(values)==1 else None
        if any(l['confidence']>=97 and l['text']=='Remaining Performance Points'
               and within(l,(400,595,710,635)) for l in lines):
            boundaries=(335,425,535,625,720,815);remaining={}
            for i,field in enumerate(CURRENCIES):
                values={number(l) for l in lines if within(l,(boundaries[i],632,boundaries[i+1],678))
                        and number(l) is not None}
                remaining[field]=values.pop() if len(values)==1 else None
            facts['remaining_performance_points']=remaining
    if screen=='career_account_totals':
        # Post-career account awards are not another race or career stat gain.
        facts['counts_as_career_action']=False
        for name,box in (('account_fans',(275,400,850,465)),('monthly_fans',(275,535,850,590))):
            matches=[re.fullmatch(r'([\d,]+)\s*\(\s*\+([\d,]+)\s*\)',l['text'])
                     for l in lines if l['confidence']>=97 and within(l,box)]
            values={(int(m[1].replace(',','')),int(m[2].replace(',',''))) for m in matches if m}
            if len(values)==1:
                total,increase=values.pop();facts[name]=dict(total=total,increase=increase)
        if 'monthly_fans' in facts:
            facts['monthly_fans']['scope']='club' if any(l['text']=='Club' and l['confidence']>=97
                                                       and within(l,(300,500,400,540)) for l in lines) else 'unknown'
        levels={number(l) for l in lines if within(l,(480,205,550,255)) and number(l) is not None}
        if len(levels)==1:facts['bond_level']=levels.pop()
        matches=[re.fullmatch(r'([\d,]+)/([\d,]+)\(\+([\d,]+)\)',l['text'])
                 for l in lines if l['confidence']>=97 and within(l,(550,225,850,280))]
        values={tuple(int(part.replace(',','')) for part in m.groups()) for m in matches if m}
        if len(values)==1:
            current,required,increase=values.pop()
            if 0<=current<=required and required>0:facts['bond_progress']=dict(current=current,required=required,increase=increase)
    if screen=='training_result':
        failures=[l for l in lines if l['text']=='FAILURE' and l['confidence']>=97
                  and within(l,(300,600,800,790)) and l['box'][3]-l['box'][1]>=40]
        if failures:facts.update(training_outcome='failure',failure_banner=failures)
        gains={}; totals={};caps={};gain_candidates={};result_candidates={};digit_crosschecks=[];partial_results={}
        for field in FIELDS:
            r=regions.get('gain.'+field,{})
            m=re.fullmatch(r'\+(\d{1,3})',r.get('text',''))
            if m and r['confidence']>=98:gains[field]=int(m[1])
            wide=regions.get('wide_gain.'+field,{})
            m=re.fullmatch(r'\+(\d{1,3})',wide.get('text',''))
            if m and wide['confidence']>=97:gains[field]=int(m[1])
            expanded=regions.get('expanded_gain.'+field,{})
            expanded_match=re.fullmatch(r'\+(\d{1,3})',expanded.get('text',''))
            if expanded_match and expanded['confidence']>=98:gains[field]=int(expanded_match[1])
            result_region=regions.get('result.'+field,{})
            signed_result=re.fullmatch(r'\+(\d{1,3})',result_region.get('text',''))
            if signed_result and result_region['confidence']>=98:gains[field]=int(signed_result[1])
            tight_digits=number(result_region)
            signed=re.fullmatch(r'\+(\d{1,3})',r.get('text',''))
            if signed and r['confidence']>=80 and tight_digits==int(signed[1]):
                gains[field]=tight_digits;digit_crosschecks.append(field)
            scaled=[regions.get(f'scaled_gain_{i}.'+field,{}) for i in range(3)]
            scaled_values={int(m[1]) for observation in scaled if observation.get('confidence',0)>=98 and (m:=re.fullmatch(r'\+(\d{1,3})',observation.get('text','')))}
            complete=[value for value in scaled_values if all(str(value).startswith(str(other)) for other in scaled_values)]
            if len(complete)==1:gains[field]=complete[0]
            candidates=[]
            for observation in (r,wide,result_region,expanded,*scaled):
                match=re.fullmatch(r'\+(\d{1,3})',observation.get('text',''))
                if match and observation['confidence']>=90:candidates.append(int(match[1]))
            gain_candidates[field]=sorted(set(candidates))
            if field in gains and gains[field] not in gain_candidates[field]:gain_candidates[field].append(gains[field])
            r=regions.get('result.'+field,{})
            m=re.fullmatch(r'(\d{1,4})/(\d{3,4})',r.get('text',''))
            if m and 1000<=int(m[2]) and int(m[1])<=int(m[2]):
                if r['confidence']>=90:result_candidates[field]=int(m[1])
                if r['confidence']>=97:totals[field]=int(m[1]);caps[field]=int(m[2])
            if field!='skill_points':
                from .result_counter import partial_counter
                partials=[]
                for view,observation in (('original',r),('padded',raw.get('result_numerator_refinement',{}).get('result.'+field,{}))):
                    partial=partial_counter(observation)
                    if partial:partials.append(dict(partial,source_view=view))
                if partials:partial_results[field]=partials
        totals['skill_points']=number(regions.get('result.skill_points',{}))
        result_candidates['skill_points']=number(regions.get('result.skill_points',{}),90)
        facts.update(training_gains=gains,result_values=totals,stat_caps=caps,training_gain_candidates=gain_candidates,result_value_candidates=result_candidates,gain_digit_crosschecks=digit_crosschecks)
        if partial_results:
            facts['partial_result_counter_readings']=partial_results
            facts['result_numerator_candidates']={field:sorted({p['value'] for p in views}) for field,views in partial_results.items()}
        performance={}
        for field in CURRENCIES:
            observation=regions.get('performance_gain.'+field,{})
            match=re.fullmatch(r'\+(\d{1,3})',observation.get('text',''))
            if match and observation['confidence']>=97:performance[field]=int(match[1])
        if performance:facts['awarded_performance_gains']=performance
    if screen=='lesson_confirmation':
        gains={}
        boundaries=(276,370,465,559,652,745,831)
        for i,field in enumerate(FIELDS):
            candidates=[l for l in lines if l['confidence']>=97 and within(l,(boundaries[i],403,boundaries[i+1],431))]
            matches=[re.fullmatch(r'(?:\(\s*)?\+\s*(\d+)(?:\s*\))?',l['text']) for l in candidates]
            numbers={int(m[1]) for m in matches if m}
            if len(numbers)==1:gains[field]=numbers.pop()
        facts.update(name_candidates=[l['text'].strip() for l in lines if within(l,(280,85,650,125)) and l['confidence']>=95 and any(c.isalpha() for c in l['text'])],
                     projected_performance_points=currencies(True),current_stats=values('modal_current'),
                     projected_stat_gains=gains,
                     projected_effects=preview_effects([l for l in lines if l['confidence']>=97 and within(l,(440,130,820,245))]),awarded_effects=[])
        for effect in facts['projected_effects']:
            match=re.fullmatch(r'(Friendship Training Effectiveness|Specialty Priority|Support Chain Event Frequency Lvl)\s*\+\s*(\d+)%?',effect['raw_text'])
            if match:effect.update(field={'Friendship Training Effectiveness':'friendship_training_effectiveness','Specialty Priority':'specialty_priority','Support Chain Event Frequency Lvl':'support_chain_event_frequency'}[match[1]],amount=int(match[2]))
    if screen=='lesson_selection':
        facts.update(performance_points=currencies(),available_effects=preview_effects(lines))
    facts.update(performance_panel_facts(lines, screen, stats))
    if facts.get('training_outcome')=='failure':
        facts['unawarded_performance_projection']=facts.pop('awarded_performance_gains',{})
    if screen in ('skill_selection','skill_confirmation','skill_receipt'):
        facts.update(points_semantics='possibly_projected_remaining_points',spent_skill_points=None,item_list_complete=False)
        labels=[l for l in lines if l['text']=='Skill Points' and l['confidence']>=97]
        if len(labels)==1:
            label=labels[0];candidates=[l for l in lines if within(l,(label['box'][2],label['box'][1]-10,850,label['box'][3]+10)) and number(l) is not None]
            if len(candidates)==1:facts['displayed_skill_points']=number(candidates[0])
        if screen=='skill_selection' and 'skill_point_refinement' in raw:
            from .refine_skill_points import counter_reading
            value,conflicts=counter_reading(raw['skill_point_refinement'],facts.get('displayed_skill_points'))
            if conflicts:
                facts.pop('displayed_skill_points',None)
                facts['skill_point_conflict']=conflicts
            elif value is not None:
                facts['displayed_skill_points']=value
                facts['skill_point_refinement_basis']='same_frame_counter_crops'
        if screen=='skill_confirmation':
            # Fixed card headings only; a scrollbar means this cannot establish list completeness.
            facts['visible_skill_names']=[l['text'].strip() for l in lines if l['confidence']>=97 and 365<=l['box'][0]<=390 and any(within(l,(365,y-15,740,y+15)) for y in (129,283,437,590,743))]
        if screen=='skill_selection':
            cards=[]
            controls=[l for l in lines if within(l,(690,395,800,875)) and ((number(l) is not None and l['box'][2]>=766) or (l['text']=='Obtained' and l['confidence']>=90))]
            for control in controls:
                cy=(control['box'][1]+control['box'][3])/2
                headings=[l for l in lines if l['confidence']>=95 and 355<=l['box'][0]<=380 and within(l,(360,cy-65,680,cy-35))
                          and not re.match(r'(?:Slightly|Moderately|Increase|Recover|Gain|Control)\b',l['text'])]
                if len(headings)==1:
                    heading=headings[0];name=re.sub(r'\s*[○◯◎]\s*$','',heading['text']).strip()
                    variants={v['variant'] for v in raw.get('skill_variants',[]) if v['name_box']==heading['box']}
                    if variants:name=name.removesuffix('�').rstrip()
                    cards.append(dict(name=name,name_box=heading['box'],displayed_cost=number(control),
                                      menu_status='obtained_or_selected' if control['text']=='Obtained' else 'available',
                                      variant=variants.pop() if len(variants)==1 else None,variant_verified=False))
            facts['skill_cards']=cards
    if screen=='race_result':
        item_headers=[l for l in lines if l['text']=='Items' and l['confidence']>=97
                      and within(l,(250,500,830,900))]
        quantities=[]
        if len(item_headers)==1:
            for line in lines:
                match=re.fullmatch(r'[x\u00d7]\s*(\d{1,6})',line['text'])
                if (match and line['confidence']>=97 and
                    within(line,(260,item_headers[0]['box'][3],825,940))):
                    quantities.append(dict(quantity=int(match[1]),name=None,
                        box=line['box'],raw_text=line['text'],confidence=line['confidence']))
        facts['visible_item_quantities']=quantities
        m=re.search(r'Fans\s+([\d,]+)\s*\(\+([\d,]+)\)',text,re.I)
        facts.update(fans=int(m[1].replace(',','')),fans_gained=int(m[2].replace(',','')))
        names=[re.sub(r'^(?:DEBUT|G[123]|OP|PRE-OP|EX)\s+','',l['text'],flags=re.I) for l in lines if l['confidence']>=95 and within(l,(280,425,810,456))
               and not re.fullmatch(r'DEBUT|G[123]|OP|PRE-OP|EX',l['text'],re.I)]
        places=[re.fullmatch(r'(\d{1,2})(?:st|nd|rd|th)',l['text'],re.I) for l in lines if l['confidence']>=95 and within(l,(280,160,550,355))]
        places={int(m[1]) for m in places if m}
        descriptions=[l['text'] for l in lines if l['confidence']>=97 and within(l,(260,457,810,495)) and re.search(r'\b(?:Turf|Dirt)\b',l['text'])]
        course=re.search(r'^(.+?)\s+(Turf|Dirt)\s+(\d+)m\s+\(([^)]+)\)\s+(Right|Left|Straight)(?:\s*/\s*(Outer|Inner))?',' '.join(descriptions),re.I)
        conditions=[l['text'].lower() for l in lines if l['confidence']>=97
                    and within(l,(690,457,810,495))
                    and re.fullmatch(r'Firm|Good|Heavy',l['text'],re.I)]
        facts.update(race_name=names[0] if len(names)==1 else None,placing=places.pop() if len(places)==1 else None,
            course=dict(venue=course[1],surface=course[2].lower(),distance_m=int(course[3]),distance_category=course[4].lower(),direction=course[5].lower(),variant=course[6].lower() if course[6] else None) if course else None,
            course_condition=conditions[0] if course and len(conditions)==1 else None,
            item_rewards_complete=False)
    titles=[l['text'] for l in lines if within(l,(240,195,850,245)) and l['confidence']>=95 and l['box'][3]<=250 and l['text']!='MAX']
    candidate_titles=[l['text'] for l in lines if within(l,(240,195,850,245)) and l['confidence']>=90 and l['box'][3]<=250 and l['text']!='MAX']
    return dict(screen=screen,stats=stats,training_option=option if screen=='training_result' else None,
                effects=effects,facts=facts,completed_action='training' if screen=='training_result' else None,
                context_title=' '.join(titles) or None,context_title_candidate=' '.join(candidate_titles) or None,ocr={'neural':lines})
