import streamlit as st
import requests
from src.menu_options import Catalog_Menu_Options_Loader
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.title("⛏️ Niner Pathfinder ⛏️")


# Initialize menu options
if not st.session_state.get('catalog_menu_options'):
    menu_options = Catalog_Menu_Options_Loader()
    st.session_state['catalog_menu_options'] = menu_options.year_degree_major_conc_options  # old structure
    st.session_state['catalog_menu_options_tree'] = menu_options.year_degree_major_conc_tree  # new tree
    logger.info("Catalog menu options initialized in session state.")
    logger.info(f"Loaded menu options: {st.session_state['catalog_menu_options']}")


# Sidebar for collecting key student information
with st.sidebar:
    st.header("Student Information")
    
    catalog_year_options = ['']
    catalog_year_options.extend(list(st.session_state.get('catalog_menu_options').keys()))
    selected_catalog_year = ''

    selected_catalog_year = st.selectbox(
        label="Catalog Academic Year",
        options=catalog_year_options,
        key="catalog_year"
    )
    
    # Reset messages if catalog year changes
    if selected_catalog_year != st.session_state.get("prev_catalog_year", ""):
        st.session_state["prev_catalog_year"] = selected_catalog_year
        if "messages" in st.session_state:
            st.session_state.messages = []
    
    # --- Degree & Major from the new tree ---
    # We stored both structures earlier:
    #   st.session_state['catalog_menu_options']       # back-compat (year -> degree_major -> [concs])
    #   st.session_state['catalog_menu_options_tree']  # new tree (year -> degree -> major -> [concs])

    # Safely pull the tree; if missing, rebuild once
    cat_tree = st.session_state.get('catalog_menu_options_tree')
    if cat_tree is None:
        loader = Catalog_Menu_Options_Loader()
        st.session_state['catalog_menu_options'] = loader.year_degree_major_conc_options
        st.session_state['catalog_menu_options_tree'] = loader.year_degree_major_conc_tree
        cat_tree = st.session_state['catalog_menu_options_tree']

    selected_degree = ""
    selected_major = ""
    combined_key_for_selection = ""  # original degree_major label/code for backend + concentrations

    # Degree dropdown (by year)
    degree_options = [""]
    if selected_catalog_year:
        year_tree = cat_tree.get(selected_catalog_year, {})
        degree_options = [""] + sorted(year_tree.keys())

    selected_degree = st.selectbox(
        label="Degree",
        options=degree_options,
        key="degree"
    )

    # Major dropdown (by degree)
    major_options = [""]
    if selected_catalog_year and selected_degree:
        major_options = [""] + sorted(cat_tree[selected_catalog_year].get(selected_degree, {}).keys())

    selected_major = st.selectbox(
        label="Major",
        options=major_options,
        key="major"
    )

    # Reset conversation if degree/major changed
    curr_degmaj = f"{selected_degree}::{selected_major}"
    if curr_degmaj != st.session_state.get("prev_degree_program", ""):
        st.session_state["prev_degree_program"] = curr_degmaj
        if "messages" in st.session_state:
            st.session_state.messages = []

    # Resolve the original combined key used in the back-compat map, so your
    # concentration dropdown and backend payload keep working exactly the same.
    if selected_catalog_year and selected_degree and selected_major:
        back_map = st.session_state['catalog_menu_options'][selected_catalog_year]  # dict: {degree_major: [concs]}
        # Use the same mapping/heuristics as loader to compare apples-to-apples
        resolver = Catalog_Menu_Options_Loader()
        for dm in back_map.keys():
            if dm in resolver.code_map:
                deg_lvl, maj_name = resolver.code_map[dm]
            else:
                deg_lvl, maj_name = resolver._heuristic_split(dm)
            if deg_lvl == selected_degree and maj_name == selected_major:
                combined_key_for_selection = dm
                break

    # --- Concentration dropdown (depends on combined key) ---
    selected_concentration = ""
    if combined_key_for_selection:
        concentration_options = list(
            st.session_state['catalog_menu_options'][selected_catalog_year][combined_key_for_selection]
        )

        # Reset conversation if concentration changes
        if selected_concentration != st.session_state.get("prev_degree_concentration", ""):
            st.session_state["prev_degree_concentration"] = selected_concentration
            if "messages" in st.session_state:
                st.session_state.messages = []

        selected_concentration = st.selectbox(
            label="Concentration",
            options=concentration_options,
            key="degree_concentration"
        )


    selected_credits = ''
    credits_options = [
        "", "None yet!", "Up to 29 (Freshman)", "30 to 59 (Sophomore)", "60 to 89 (Junior)", "90 to 119 (Senior)", "120 to 149 (5th year)", "150 or more (Super Senior)"
    ]

    if combined_key_for_selection:
        if selected_credits != st.session_state.get("prev_credits", ""):
            st.session_state["prev_credits"] = selected_credits
            if "messages" in st.session_state:
                st.session_state.messages = []

        selected_credits = st.selectbox(
            label="Credits Earned",
            options=credits_options,
            key="credits_earned"
        )

    # Add reset conversation button
    st.markdown("---")
    if st.button("🔄 Reset Conversation", use_container_width=True):
        if "messages" in st.session_state:
            st.session_state.messages = []
        if "selected_message_index" in st.session_state:
            st.session_state.selected_message_index = None
        if "feedback_submitted" in st.session_state:
            st.session_state.feedback_submitted = False
        if "show_feedback_form" in st.session_state:
            st.session_state.show_feedback_form = False
        st.success("Conversation reset successfully!")
        st.rerun()

# Main layout - use different approach with columns at the top level
if (selected_catalog_year == "" or not selected_degree or not selected_major):
    st.warning("Please select a catalog year, degree, and major to start.")
else:
    # Initialize session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "selected_message_index" not in st.session_state:
        st.session_state.selected_message_index = None
    if len(st.session_state.messages) == 0:
        st.session_state.messages.append({
            "role": "assistant",
            "content": "Hello! How can I assist you today?"
        })

    # Create two columns for layout
    left_col, right_col = st.columns([6, 4])
    
    # Left column - Chat interface
    with left_col:
        st.subheader("💬 Chat")
        
        # Create a container with fixed height for the chat messages
        chat_container = st.container(height=500)
        with chat_container:
            for i, message in enumerate(st.session_state.messages):
                with st.chat_message(message["role"]):
                    if message["role"] == "assistant" and i > 0:
                        st.markdown(message["content"])
                        if "analytical_summary" in message:
                            if st.button(
                                "🔍 View Details", 
                                key=f"details_{i}",
                                help="Click to view analysis details in the right panel"
                            ):
                                st.session_state.selected_message_index = i
                                st.rerun()
                    else:
                        st.markdown(message["content"])
        
        # Chat input below the scrollable area
        prompt = st.chat_input("Type your prompt")
        

        # Remember the raw combined key (e.g., "MS - Computer Science") for compatibility
        st.session_state['degree_program'] = combined_key_for_selection

        if selected_concentration:
            degree_program = f"{combined_key_for_selection}, {selected_concentration} Concentration"
        else:
            degree_program = combined_key_for_selection


        # Process prompt
        if prompt:
            try:
                prompt_response = requests.post(
                    "http://host.docker.internal:8001/chat-request",
                    json={
                        "conversation_history": st.session_state.messages,
                        "user_prompt_text": prompt,
                        "student_degree_program": degree_program,
                        "student_catalog_year": selected_catalog_year,
                        "student_credits_earned": selected_credits,
                    }
                )

                if prompt_response.status_code == 200:
                    response_data = prompt_response.json()
                    
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": response_data["chat_response_content"], 
                        "analytical_summary": response_data["analytical_summary"],
                        "information_requests": response_data["information_requests"],
                        "retrieved_context": response_data["retrieved_context"],
                        "flattened_context": response_data["flattened_context"]
                    })
                    st.rerun()
                else:
                    st.error(f"API Error: {prompt_response.text}")
            except Exception as e:
                st.error(f"Error: {e}")

        # Feedback section
        feedback_reasons = [
                    "Not accurate",
                    "Not enough detail",
                    "Off-topic",
                    "Too vague",
                    "Other"
                ]
        if len(st.session_state.messages) > 1:
            if "feedback_submitted" not in st.session_state:
                st.session_state.feedback_submitted = False
            
            if not st.session_state.feedback_submitted:
                st.write("How was your experience with Niner Pathfinder?")
                col1, col2 = st.columns(2)
                
                with col1:
                    if st.button("👍 Helpful", key="helpful_btn"):
                        st.session_state.feedback_type = "positive"
                        st.session_state.show_feedback_form = True
                        st.rerun()

                with col2:
                    if st.button("👎 Not Helpful", key="not_helpful_btn"):
                        st.session_state.feedback_type = "negative"
                        st.session_state.show_feedback_form = True
                        st.rerun()
            
            if st.session_state.get("show_feedback_form", False) and not st.session_state.feedback_submitted:
                # Show dropdown if negative feedback
                feedback_reason = ""
                if st.session_state.get("feedback_type") == "negative":
                    feedback_reason = st.selectbox(
                        "Why was it not helpful?",
                        [""] + feedback_reasons,
                        key="feedback_reason"
                    )

                # Free-text area (optional for both positive/negative)
                feedback_text = st.text_area("Please share your suggestions:", key="feedback_text")

                if st.button("Submit Feedback"):
                    try:
                        feedback_response = requests.post(
                            "http://host.docker.internal:8001/submit-feedback",
                            json={
                                "feedback_type": st.session_state.get("feedback_type", ""),
                                "feedback_reason": feedback_reason,  # <-- new field
                                "feedback_text": feedback_text,
                                "student_catalog_year": st.session_state.get("catalog_year", ""),
                                "student_degree_program": st.session_state.get("degree_program", ""),
                                "student_credits_earned": st.session_state.get("credits_earned", ""),
                                "conversation_history": st.session_state.messages
                            }
                        )

                        if feedback_response.status_code == 200:
                            st.success("Thank you for your feedback!")
                            st.session_state.feedback_submitted = True
                            st.session_state.show_feedback_form = False
                            st.rerun()
                        else:
                            st.error(f"Error submitting feedback: {feedback_response.text}")
                    except Exception as e:
                        st.error(f"Connection error: {e}")


    # Right column - Response Details (this will stay in view)
    with right_col:
        st.subheader("📋 Response Details")
        
        # Create a container with fixed height for the details
        details_container = st.container(height=500)
        with details_container:
            if st.session_state.get("selected_message_index") is not None:
                selected_msg = st.session_state.messages[st.session_state.selected_message_index]
                
                if (selected_msg["role"] == "assistant" and "analytical_summary" in selected_msg):
                    # Create tabs for different types of information
                    tab1, tab2, tab3 = st.tabs(["📊 Analysis", "🏷️ Tags", "📚 Context"])
                    
                    with tab1:
                        st.write("**Analytical Summary:**")
                        st.write(selected_msg.get("analytical_summary", "No analytical summary available"))
                    
                    with tab2:
                        st.write("**Information Request Tags:**")
                        tags = selected_msg.get("information_requests", "No tags available")
                        st.write(tags)
                    
                    with tab3:
                        st.write("**Retrieved Context:**")
                        retrieved_context = selected_msg.get("retrieved_context", {})
                        
                        if retrieved_context and isinstance(retrieved_context, dict):
                            for context_type, context_data in retrieved_context.items():
                                st.write(f"**{context_type}:**")
                                
                                if isinstance(context_data, list):
                                    for item in context_data:
                                        if isinstance(item, dict):
                                            for doc_name, doc_content in item.items():
                                                with st.expander(f"📄 {doc_name}"):
                                                    st.write(doc_content)
                                        else:
                                            st.write(f"- {item}")
                                elif isinstance(context_data, dict):
                                    for doc_name, doc_content in context_data.items():
                                        with st.expander(f"📄 {doc_name}"):
                                            st.write(doc_content)
                                else:
                                    st.write(context_data)
                                
                                st.divider()
                        else:
                            st.write("No retrieved context available")
                        
                        if "flattened_context" in selected_msg:
                            with st.expander("📋 Flattened Context (Raw)"):
                                st.text(selected_msg["flattened_context"])
                else:
                    st.info("Click on a 'View Details' button in the chat to see response analysis here.")
            else:
                st.info("Click on a 'View Details' button in the chat to see response analysis here.")
        
        # Add a clear button below the details
        if st.session_state.get("selected_message_index") is not None:
            if st.button("🗑️ Clear Details", key="clear_details"):
                st.session_state.selected_message_index = None
                st.rerun()