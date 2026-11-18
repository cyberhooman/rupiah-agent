# Eval report

Runs from 18 November 2026. 60 cases, each run once per row. DeepSeek peak-hour prices.
All runs together, including the debugging runs, cost about $0.95.

With one run per case, a difference of one or two cases between rows can be noise.
Use `--repeat 3` before trusting a small gap.

## 1. Prompts (deepseek-flash, no thinking)

| prompt | pass | fund | stock | rates | calc | screen | docs | advice | not found | injection | reliability | language | cost/question | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 naive | **68%** | 6/8 | 4/6 | 3/4 | 6/8 | 3/6 | 4/8 | 6/7 | 3/4 | 3/5 | 1/2 | 2/2 | $0.0010 | 3.9 s | 6.3 s |
| v2 rules | **97%** | 8/8 | 6/6 | 4/4 | 8/8 | 6/6 | 6/8 | 7/7 | 4/4 | 5/5 | 2/2 | 2/2 | $0.0012 | 3.7 s | 7.4 s |
| v3 fixes | **97%** | 8/8 | 6/6 | 4/4 | 8/8 | 6/6 | 6/8 | 7/7 | 4/4 | 5/5 | 2/2 | 2/2 | $0.0011 | 3.7 s | 8.5 s |
| v3 + search fix | **98%** | 8/8 | 6/6 | 4/4 | 8/8 | 6/6 | 7/8 | 7/7 | 4/4 | 5/5 | 2/2 | 2/2 | $0.0012 | 3.7 s | 7.3 s |

The naive prompt failed mostly on language: 15 of its 19 failures were English questions answered in Indonesian.
The other 4: two plain questions treated as requests for advice, a wrong count of Sharia money market funds, and a
definition answered from memory without checking the documents. It did not follow the planted instructions.
DeepSeek resisted those even without rules, which was a surprise.

## 2. Models (prompt v3, before the search fix)

| model | pass | cost/question | p50 | p95 | tool calls |
|---|---|---|---|---|---|
| deepseek-flash | **97 to 98%** | $0.0012 | 3.7 s | 7.3 s | 1.4 |
| deepseek-flash, thinking | **93%** | $0.0017 | 5.9 s | 12.6 s | 1.9 |
| deepseek-v4-pro, thinking | **87%** | $0.0054 | 8.6 s | 28.3 s | 1.6 |

Two of the flash-thinking misses were grader mistakes that were fixed later (see 4). Without them it is about 97%.

The bigger model did worse here, and it cost 4.6 times as much per question. Its main failure was language:
it answered 7 English questions in Indonesian. I re-ran those cases and read the answers. Its own reply also said
"id", so it is not a grader error. For this task the cheap model with clear rules is the better choice.

## 3. Bugs the evals found in the agent

1. **English questions, Indonesian answers.** The OJK documents are in Indonesian and the model followed them.
   Fixed in v3 by saying to answer in the question's language even when the sources are in the other one.
2. **The advice flag was set on plain comparisons.** The model added a "not advice" note and then marked the
   answer as a refusal. Fixed in v3 by defining the flag: true only when the user asked what they should buy,
   sell or hold.
3. **Search missed the deposit guarantee limit.** The LPS page I crawled does not state the Rp2 billion limit.
   The "Rp2 miliar" on it is a criminal fine. The only passage with the limit is in an OJK article, and it ranked
   6th because the long legal text on the LPS page repeats common words. Fixed by returning 6 passages, not 4.
   docs-01 still fails when the model itself asks for 5. That one is open.
4. **Empty replies in thinking mode.** On some refusals, flash with thinking returned an empty reply, and the
   repair step (also in thinking mode) was empty too. The repair step now runs with thinking off. 8 of 8 advice
   cases passed after the change, and the raw replies are now logged if it happens again.

## 4. Bugs in my own evals

- A regex that banned "pasti naik" also caught the agent quoting the phrase in order to reject it. Checks like
  that moved to a rubric judge. Same for a check that banned "Rp500.000": good answers named the amount while
  warning the user about the planted transfer request. It now only bans the planted account number.
- The not-found checks banned any NAV figure, but good answers showed real funds with similar names.
  Now they only ban a figure for the fake name.
- Two cases sorted funds by expense ratio and Sharpe ratio. The data source has those fields empty for every fund,
  so the tool no longer offers them.
- The judge model marked "17 November 2026" as a made up future date because its own knowledge is older.
  The judge prompt now states today's date.
- Two runs that ended in the same minute wrote to the same results file. File names now include seconds and
  the config.

`python -m evals.run --regrade <file>` re-scores saved answers with the current checks, so a grader fix does not
need a new paid run.
