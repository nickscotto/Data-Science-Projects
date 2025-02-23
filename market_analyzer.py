import os
import streamlit as st
import pandas as pd
from langchain_community.vectorstores import Chroma
from langchain_community.chat_message_histories import StreamlitChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
import plotly.express as px

# Streamlit Config
st.set_page_config(page_title="Steve Li Flow", layout="wide")
st.title("Podcast Competitor Analyzer")

# Load API Keys from Streamlit Secrets
try:
    YOUTUBE_API_KEY = st.secrets["youtube"]
    OPENAI_API_KEY = st.secrets["openai"]
except KeyError:
    st.error("API keys for YouTube and OpenAI are missing in Streamlit secrets. Please configure them in your secrets.toml or Streamlit Cloud settings.")
    st.stop()

# Load Metadata
if os.path.exists('competitor_podcast_videos.csv'):
    df = pd.read_csv('competitor_podcast_videos.csv')
else:
    st.error("Run the data collection script first to generate 'competitor_podcast_videos.csv'.")
    st.stop()

# Competitors List with Creator Mapping
COMPETITORS = {
    "Russell Brand": "Russell Brand – Stay Free",
    "Dr. Rangan Chatterjee": "Dr. Rangan Chatterjee – Feel Better, Live More",
    "Jay Shetty": "Jay Shetty – On Purpose",
    "Tom Bilyeu": "Tom Bilyeu – Impact Theory",
    "Lewis Howes": "Lewis Howes – The School of Greatness",
    "Vishen Lakhiani": "Mindvalley – Vishen Lakhiani",
    "Steven Bartlett": "Steven Bartlett – Diary of a CEO"
}
COMPETITOR_NAMES = list(COMPETITORS.values())

# Vector Store Setup
embedding_function = OpenAIEmbeddings(model='text-embedding-ada-002', api_key=OPENAI_API_KEY)
vectorstore = Chroma(persist_directory="data/podcast_chroma.db", embedding_function=embedding_function)
if not os.path.exists("data/podcast_chroma.db"):
    st.error("Vector store not found. Run the data collection script.")
    st.stop()

# Generate Database Summary
def generate_db_summary(df, vectorstore):
    podcast_counts = df['podcast_name'].value_counts().to_dict()
    metadata_fields = list(df.columns)
    total_episodes = len(df)
    sample_docs = vectorstore.similarity_search("common topics", k=5)
    sample_text = " ".join([doc.page_content[:200] for doc in sample_docs])
    summary = (
        f"Database Overview:\n"
        f"- Podcasts: {', '.join(COMPETITOR_NAMES)}\n"
        f"- Episode Counts: {', '.join([f'{name}: {count}' for name, count in podcast_counts.items()])}\n"
        f"- Metadata Fields: {', '.join(metadata_fields)} (e.g., view_count, like_count, title)\n"
        f"- Total Episodes: {total_episodes}\n"
        f"- Transcript Sample: Common topics include {sample_text[:100]}...\n"
        f"Use metadata for stats (e.g., view_count, top videos) and transcripts for content. "
        f"Creators map to podcasts: {', '.join([f'{k} → {v}' for k, v in COMPETITORS.items()])}."
    )
    return summary

db_summary = generate_db_summary(df, vectorstore)

# Chat History Setup
msgs = StreamlitChatMessageHistory(key="langchain_messages")
if "welcome_added" not in st.session_state:
    st.session_state.welcome_added = False
if not msgs.messages and not st.session_state.welcome_added:
    msgs.add_ai_message(f"I have data on these podcasts: {', '.join(COMPETITOR_NAMES)}. Ask me anything!")
    st.session_state.welcome_added = True

# RAG Chain Setup
def create_rag_chain():
    retriever = vectorstore.as_retriever(search_kwargs={"k": 10})
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7, api_key=OPENAI_API_KEY, max_tokens=4000)

    contextualize_q_prompt = ChatPromptTemplate.from_messages([
        ("system", "Reformulate the question as a standalone query based on chat history, using creator names (e.g., 'Jay Shetty') to infer the podcast if clear."),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    history_aware_retriever = create_history_aware_retriever(llm, retriever, contextualize_q_prompt)

    qa_system_prompt = (
        f"{db_summary}\n\n"
        "You are an assistant analyzing the podcast database. "
        "For stats questions (e.g., view counts, top videos), use the metadata fields directly. "
        "For content questions (e.g., topics, strategies), use the transcripts in the context below. "
        "Answer concisely with examples if possible. "
        "If data is missing, say 'I don’t have that info' and suggest checking the Dashboard for stats. "
        "Use chat history and creator associations (e.g., 'Jay Shetty' → 'Jay Shetty – On Purpose') to resolve queries without requiring the full podcast name if the context is clear.\n\n"
        "{context}"
    )
    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", qa_system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)
    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    return RunnableWithMessageHistory(
        rag_chain,
        lambda session_id: msgs,
        input_messages_key="input",
        history_messages_key="chat_history",
        output_messages_key="answer",
    )

rag_chain = create_rag_chain()

# Function to Handle Metadata Queries
def get_top_videos(podcast_name, top_n=5):
    if podcast_name not in COMPETITOR_NAMES:
        return f"I don’t have data on {podcast_name}. I only track: {', '.join(COMPETITOR_NAMES)}."
    filtered_df = df[df['podcast_name'] == podcast_name].sort_values('view_count', ascending=False).head(top_n)
    if filtered_df.empty:
        return f"No data found for {podcast_name}."
    result = f"Top {top_n} videos for {podcast_name}:\n"
    for i, row in filtered_df.iterrows():
        result += f"- '{row['title']}' ({row['published_at']}): {row['view_count']} views\n"
    return result

# UI Layout: Tabs
tab1, tab2, tab3 = st.tabs(["Dashboard", "Chat Analyzer", "Content Trends"])

# Tab 1: Dashboard
with tab1:
    st.subheader("Competitor Stats")
    podcast_filter = st.multiselect("Select Podcasts", df['podcast_name'].unique(), default=df['podcast_name'].unique())
    filtered_df = df[df['podcast_name'].isin(podcast_filter)]
    col1, col2 = st.columns(2)
    with col1:
        st.write("Average Engagement")
        avg_stats = filtered_df.groupby('podcast_name')[['view_count', 'like_count', 'comment_count']].mean().reset_index()
        st.dataframe(avg_stats.style.format({"view_count": "{:.0f}", "like_count": "{:.0f}", "comment_count": "{:.0f}"}))
    with col2:
        st.write("Views Over Time")
        fig = px.line(filtered_df, x='published_at', y='view_count', color='podcast_name', title="Views by Episode")
        st.plotly_chart(fig)

# Tab 2: Chat Analyzer
with tab2:
    st.subheader("Ask About Your Competitors")
    
    # Toggle for showing/hiding history
    show_history = st.checkbox("Show Conversation History", value=True)
    
    # Display history if toggled on
    if show_history:
        for msg in msgs.messages:
            st.chat_message(msg.type).write(msg.content)
    else:
        st.write("History hidden. Toggle 'Show Conversation History' to view past messages.")

    if question := st.chat_input("E.g., 'Jay Shetty’s top videos' or 'What does Tom Bilyeu talk about?'"):
        with st.spinner("Analyzing..."):
            # Add user message to history
            msgs.add_user_message(question)
            if show_history:
                st.chat_message("human").write(question)

            question_lower = question.lower()
            stats_keywords = ["view", "views", "count", "counts", "top", "popular", "performance", "videos"]
            if any(keyword in question_lower for keyword in stats_keywords):
                # Try to match creator name to podcast
                podcast_name = None
                for creator, full_name in COMPETITORS.items():
                    if creator.lower() in question_lower or full_name.lower().split(" – ")[1] in question_lower:
                        podcast_name = full_name
                        break
                # Use chat history if no match in current query
                if not podcast_name:
                    last_podcast = None
                    for msg in reversed(msgs.messages[-5:]):
                        if msg.type == "human":
                            for creator, full_name in COMPETITORS.items():
                                if creator.lower() in msg.content.lower() or full_name.lower().split(" – ")[1] in msg.content.lower():
                                    last_podcast = full_name
                                    break
                            if last_podcast:
                                break
                    podcast_name = last_podcast
                if podcast_name:
                    response = get_top_videos(podcast_name)
                else:
                    response = "Please specify a podcast or creator name from: " + ", ".join(COMPETITOR_NAMES)
            else:
                # Fallback to RAG for content questions
                response = rag_chain.invoke({"input": question}, config={"configurable": {"session_id": "any"}})
                response = response['answer']

            # Add AI response to history
            msgs.add_ai_message(response)
            if show_history:
                st.chat_message("ai").write(response)

# Tab 3: Content Trends
with tab3:
    st.subheader("Trending Topics")
    topic_query = "Summarize the most common topics across all podcasts in the dataset."
    if st.button("Generate Topic Summary"):
        with st.spinner("Extracting trends..."):
            response = rag_chain.invoke({"input": topic_query}, config={"configurable": {"session_id": "any"}})
            st.write(response['answer'])
