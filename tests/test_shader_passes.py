"""Pass identity/state transport tests; fixtures are structural, not game goldens."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from core.shader_passes import declared_passes


def shader_pass(name, mode=None, **state):
    tags=[] if mode is None else [('LightMode',mode)]
    return {'m_State':{'m_Name':name,'m_Tags':{'tags':tags},**state}}


def test_each_subshader_and_pass_keeps_its_own_identity():
    parsed={'m_SubShaders':[
        {'m_Tags':{'tags':[('RenderPipeline','UniversalPipeline')]},
         'm_Passes':[shader_pass('Forward','UniversalForward'),shader_pass('Effect','MysekaiEffect')]},
        {'m_Tags':{'tags':[('Queue','Transparent')]},
         'm_Passes':[shader_pass('ForwardOnly','UniversalForward')]},
    ]}
    rows=declared_passes(parsed)
    assert [(r['subShaderIndex'],r['passIndex'],r['lightMode']) for r in rows]==[
        (0,0,'UniversalForward'),(0,1,'MysekaiEffect'),(1,0,'UniversalForward')]
    assert rows[0]['subShaderTags']=={'RenderPipeline':'UniversalPipeline'}
    assert rows[2]['subShaderTags']=={'Queue':'Transparent'}
    assert rows[1]['tags']=={'LIGHTMODE':'MysekaiEffect'}


def test_subshader_tag_is_not_an_invented_pass_membership():
    rows=declared_passes({'m_SubShaders':[{'m_Tags':{'tags':[('LightMode','MysekaiEffect')]},
        'm_Passes':[shader_pass('Untagged')]}]})
    assert len(rows)==1 and rows[0]['lightMode'] is None
    assert rows[0]['renderState']=={}


def test_shader_bound_defaults_remain_distinct_from_material_overrides():
    parsed={'m_PropInfo':{'m_Props':[{'m_Name':'_BlendSrc','m_DefValue[0]':5.0}]},
        'm_SubShaders':[{'m_Passes':[shader_pass('Effect','MysekaiEffect',
            culling={'name':'_Cull','val':2.0},zTest={'name':'<noninit>','val':4.0},
            zWrite={'name':'<noninit>','val':0.0},rtBlend0={
                'srcBlend':{'name':'_BlendSrc','val':0.0},
                'destBlend':{'name':'<noninit>','val':10.0},
                'srcBlendAlpha':{'name':'_BlendSrc','val':0.0},
                'destBlendAlpha':{'name':'<noninit>','val':1.0},
                'colMask':{'name':'<noninit>','val':14.0}})]}]}
    state=declared_passes(parsed)[0]['renderState']
    assert state['blend']['srcBlend']=={'name':'_BlendSrc','val':0.0,'default':5.0}
    assert state['blend']['srcBlendAlpha']=={'name':'_BlendSrc','val':0.0,'default':5.0}
    assert state['blend']['destBlend']['val']==10.0
    assert state['blend']['destBlendAlpha']['val']==1.0
    assert state['blend']['colMask']['val']==14.0
    assert 'default' not in state['culling']
    assert state['zTest']['val']==4.0 and state['zWrite']['val']==0.0


def test_empty_or_missing_pass_state_is_preserved_without_defaults():
    rows=declared_passes({'m_SubShaders':[{'m_Passes':[{}]}]})
    assert len(rows)==1 and rows[0]['renderState']=={}
    assert rows[0]['name'] is None and rows[0]['lightMode'] is None
    assert declared_passes({})==[]
