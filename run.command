#!/bin/zsh
# Double-click this file in Finder to launch the Crawl4AI GUI.
# It activates the conda base env, starts Streamlit, and opens your browser.

cd "$(dirname "$0")"

# Activate conda base if conda is installed (where crawl4ai + streamlit live).
# Falls back to whatever python/streamlit is on PATH otherwise.
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate base
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
  source "$HOME/anaconda3/etc/profile.d/conda.sh"
  conda activate base
fi

echo "Starting Crawl4AI GUI at http://localhost:8501 ..."
echo "Close this window or press Ctrl+C to stop."

streamlit run app.py
