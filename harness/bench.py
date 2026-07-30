#!/usr/bin/env python3
"""Orchestration benchmark harness. Manager=GPT-5 (both arms), workers=grok-4.5+kimi-k2.7-code via
sidekick (worktree-isolated). Arm A = emergent (natural-language briefs, manager integrates directly);
Arm B = formal (contract briefs + runtime integration mission: declared-output check + 1 recovery retry
+ compile-probe → integration state fed to the manager). Gates deterministic (kotlinc build + a
self-contained hidden-test main + require/forbid architecture scan). Shape-parameterized + injections.
Usage: bench.py <shape> <seed> [injection]. No fabricated numbers — every metric is a real call/build."""
from __future__ import annotations
import json, os, subprocess, sys, time, random, shutil, urllib.request, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT/"spec"; RUNS = ROOT/"runs"
KOTLINC = os.path.expanduser("~/.local/kotlinc/bin/kotlinc")
WORKERS = {"grok-4.5": ("https://api.x.ai/v1", os.environ.get("XAI_API_KEY","")),
           "kimi-k2.7-code": ("https://api.moonshot.ai/v1", os.environ.get("KIMI_API_KEY",""))}
MANAGER_MODEL = "gpt-5"
OPENAI = ("https://api.openai.com/v1/chat/completions", os.environ.get("OPENAI_API_KEY",""))
COST = {"gpt-5": (1.25e-6, 10e-6)}

def load_shape(shape):
    d = SPEC/shape
    return {"dir": d, "contract": (d/"CONTRACT.md").read_text(),
            "parts": json.loads((d/"parts.json").read_text()),
            "arch": json.loads((d/"arch.json").read_text()),
            "injections": json.loads((d/"injections.json").read_text()) if (d/"injections.json").exists() else {}}

def log(run, ev, **kw):
    (run/"trace.jsonl").open("a").write(json.dumps({"ts":round(time.time(),3),"ev":ev,**kw})+"\n")
    print(f"  [{ev}] "+json.dumps(kw)[:160], flush=True)

# ---------- manager: GPT-5 (escalate cap on empty reasoning-completions; empty=harness error, not arm-fail) ----------
def gpt(messages, run, tag, max_toks=16000):
    ci,co = COST[MANAGER_MODEL]; usd_total=0.0
    for attempt in range(4):
        cap = max_toks*(attempt+1)
        body={"model":MANAGER_MODEL,"messages":messages,"max_completion_tokens":cap}
        req=urllib.request.Request(OPENAI[0],data=json.dumps(body).encode(),
            headers={"Authorization":"Bearer "+OPENAI[1],"Content-Type":"application/json"})
        try: r=json.load(urllib.request.urlopen(req,timeout=400))
        except Exception as e: log(run,"manager_err",tag=tag,attempt=attempt,err=str(e)[:200]); time.sleep(3*(attempt+1)); continue
        u=r.get("usage",{}); usd=u.get("prompt_tokens",0)*ci+u.get("completion_tokens",0)*co; usd_total+=usd
        ch=r["choices"][0]; content=ch.get("message",{}).get("content") or ""
        log(run,"manager_call",tag=tag,in_tok=u.get("prompt_tokens",0),out_tok=u.get("completion_tokens",0),
            cap=cap,finish=ch.get("finish_reason"),empty=(not content.strip()),usd=round(usd,4))
        if content.strip(): return content, usd_total
    raise RuntimeError(f"HARNESS_ERROR empty manager completion after escalation: {tag}")

# ---------- worker: sidekick single scoped instance, worktree-isolated ----------
def worker(model, brief, out_file, run, subtask_id):
    base,key = WORKERS[model]; wdir = run/f"worker_{subtask_id}"; wdir.mkdir(parents=True,exist_ok=True)
    subprocess.run(["git","init","-q"],cwd=wdir); (wdir/"BRIEF.md").write_text(brief)
    subprocess.run(["git","add","-A"],cwd=wdir); subprocess.run(["git","commit","-qm","b","--allow-empty"],cwd=wdir)
    cmd=["sidekick","run","--json","-y","--max-subtasks","1","--concurrency","1","--approval","bypass",
         "--provider","grok","--grok-model",model,"--grok-base-url",base,"--grok-key",key,
         f"Read BRIEF.md and write ONLY the file {out_file} implementing exactly what it specifies. No other files. Kotlin, package benchspec."]
    t0=time.time()
    try:
        p=subprocess.run(cmd,cwd=wdir,capture_output=True,text=True,timeout=1200,env={**os.environ,"SIDEKICK_STATE_DIR":".sk"})
        try: env=json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except Exception: env={"raw":p.stdout[-300:]}
    except subprocess.TimeoutExpired: env={"status":"timeout"}
    code=(wdir/out_file).read_text() if (wdir/out_file).exists() else ""
    log(run,"worker_done",subtask=subtask_id,model=model,bytes=len(code),secs=round(time.time()-t0,1),status=env.get("status","?"))
    return {"subtask":subtask_id,"model":model,"file":out_file,"code":code,"secs":round(time.time()-t0,1)}

# ---------- deterministic gates ----------
def gates(solution_kt, run, arm, shape):
    d=load_shape(shape); gdir=run/f"gate_{arm}"; gdir.mkdir(parents=True,exist_ok=True)
    (gdir/"solution.kt").write_text(solution_kt); shutil.copy(d["dir"]/"hidden_tests.kt", gdir/"hidden_tests.kt")
    res={"build":False,"tests_pass":0,"tests_total":0,"arch_ok":False,"correctness":False}
    jar=gdir/"out.jar"
    b=subprocess.run([KOTLINC,str(gdir/"solution.kt"),str(gdir/"hidden_tests.kt"),"-include-runtime","-d",str(jar)],
                     capture_output=True,text=True,timeout=300)
    res["build"]=(jar.exists() and b.returncode==0); res["build_err"]=b.stderr[-800:] if not res["build"] else ""
    if res["build"]:
        t=subprocess.run(["java","-cp",str(jar),"benchspec.Hidden_testsKt"],capture_output=True,text=True,timeout=120)
        import re; m=re.search(r"RESULT (\d+)/(\d+)",t.stdout)
        if m: res["tests_pass"],res["tests_total"]=int(m.group(1)),int(m.group(2))
        res["correctness"]=res["tests_total"]>0 and res["tests_pass"]/res["tests_total"]>=0.90
    req=all(s in solution_kt for s in d["arch"].get("require",[]))
    forbid=any(s in solution_kt for s in d["arch"].get("forbid",[]))
    res["arch_ok"]=bool(req and not forbid)
    res["gate_pass"]=bool(res["build"] and res["correctness"] and res["arch_ok"])
    log(run,"gates",arm=arm,**{k:res[k] for k in ("build","tests_pass","tests_total","arch_ok","gate_pass")})
    return res

# ---------- one arm ----------
def run_arm(shape, arm, seed, run, injection=None):
    run.mkdir(parents=True,exist_ok=True); d=load_shape(shape); contract=d["contract"]; PARTS=d["parts"]
    inj = d["injections"].get(injection) if injection else None
    rng=random.Random(seed); assign={f:rng.choice(list(WORKERS)) for f,_ in PARTS}
    log(run,"assignment",arm=arm,shape=shape,seed=seed,injection=injection,map=assign)
    parts={}
    for f,desc in PARTS:
        model=assign[f]
        if arm=="B":
            brief=f"Implement {desc} for `{shape}`.\n\nEXACT CONTRACT (match signatures verbatim):\n{contract}\n\nWrite ONLY {f}. package benchspec."
        else:
            brief=f"We are building `{shape}` in Kotlin (package benchspec). Implement {desc}. It will be combined with sibling parts per this contract:\n{contract}\n\nWrite ONLY {f}."
        if inj and inj["part"]==f: brief += "\n\n"+inj["brief_suffix"]   # injection perturbs exactly one worker
        parts[f]=worker(model,brief,f,run,f.replace('.kt',''))
    integ_state=""
    if arm=="B":
        for f,desc in PARTS:
            if len(parts[f]["code"].strip())<10:
                log(run,"recovery_retry",file=f,model=parts[f]["model"])
                parts[f]=worker(parts[f]["model"],f"Implement {desc}.\n\nEXACT CONTRACT:\n{contract}\n\nWrite ONLY {f}. package benchspec.",f,run,f.replace('.kt','')+"_retry")
        pd=run/"integ_probe_B"; pd.mkdir(parents=True,exist_ok=True)
        (pd/"parts.kt").write_text("\n\n".join(parts[f]["code"] for f,_ in PARTS))
        pb=subprocess.run([KOTLINC,str(pd/"parts.kt"),"-d",str(pd/"p.jar")],capture_output=True,text=True,timeout=300)
        declared={f:(len(parts[f]["code"].strip())>10) for f,_ in PARTS}
        integ_state=("RUNTIME INTEGRATION STATE (from the integration mission — use it to guide the merge):\n"
                     f"- declared outputs present: {declared}\n- concatenated-parts compile: {'OK' if pb.returncode==0 else 'ERRORS'}\n"
                     f"- compiler messages:\n{(pb.stderr or pb.stdout)[-1200:]}\n\n")
        log(run,"integration_state",compile_ok=(pb.returncode==0),declared=declared)
    joined="\n\n".join(f"// === {f} ({parts[f]['model']}) ===\n{parts[f]['code']}" for f,_ in PARTS)
    sys_p=("You are the integration manager. You do NOT rewrite features from scratch; you merge worker parts "
           "into ONE valid Kotlin file solution.kt, package benchspec, matching the contract. Resolve duplicate/"
           "conflicting declarations and ensure the contract holds. Output ONLY the Kotlin file content, no fences.")
    usr_p=f"{integ_state}CONTRACT:\n{contract}\n\nWORKER PARTS:\n{joined}\n\nProduce the merged solution.kt."
    sol,usd=gpt([{"role":"system","content":sys_p},{"role":"user","content":usr_p}],run,f"integrate_{arm}")
    sol=sol.strip()
    if sol.startswith("```"): sol="\n".join(sol.splitlines()[1:-1]) if sol.endswith("```") else sol.split("```",2)[1]
    (run/f"solution_{arm}.kt").write_text(sol)
    g=gates(sol,run,arm,shape)
    result={"arm":arm,"shape":shape,"seed":seed,"injection":injection,"assignment":assign,"gates":g,
            "manager_usd":round(usd,4),"worker_secs":round(sum(p["secs"] for p in parts.values()),1),"solution_bytes":len(sol)}
    (run/f"result_{arm}.json").write_text(json.dumps(result,indent=2))
    log(run,"arm_done",arm=arm,gate_pass=g["gate_pass"],usd=result["manager_usd"],worker_secs=result["worker_secs"])
    return result

def main():
    shape=sys.argv[1]; seed=int(sys.argv[2]); injection=sys.argv[3] if len(sys.argv)>3 else None
    tag=f"{shape}_seed{seed}"+(f"_inj-{injection}" if injection else "")
    run=RUNS/f"{tag}_{int(time.time())}"; run.mkdir(parents=True,exist_ok=True)
    log(run,"start",shape=shape,seed=seed,injection=injection,manager=MANAGER_MODEL,workers=list(WORKERS))
    a=run_arm(shape,"A",seed,run/"armA",injection); b=run_arm(shape,"B",seed,run/"armB",injection)
    summary={"tag":tag,"shape":shape,"seed":seed,"injection":injection,"armA":a,"armB":b}
    (run/"summary.json").write_text(json.dumps(summary,indent=2))
    log(run,"done",A_gate=a["gates"]["gate_pass"],B_gate=b["gates"]["gate_pass"])
    print("SUMMARY "+json.dumps({"tag":tag,"A":a["gates"]["gate_pass"],"B":b["gates"]["gate_pass"]}))

if __name__=="__main__": main()
