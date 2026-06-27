import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  FlatList,
  ActivityIndicator,
  SafeAreaView,
  StatusBar,
  Dimensions,
  Animated,
  KeyboardAvoidingView,
  Platform,
  Linking,
  Modal,
  ScrollView,
  Image,
  Switch,
  Alert,
} from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Clipboard from 'expo-clipboard';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';

// ================= البيانات الأساسية الثابتة =================
const TOKEN = "IVlSFv6JwO2TttyAhMW6Cu9/eMCDQhcfY0uHWu000SDnAyEwsYxtR8rFADgo22LM";
const RAILWAY_SERVER_URL = "https://web-production-c09dc.up.railway.app";
const POW_API_URL = `${RAILWAY_SERVER_URL}/pow`;

const { width } = Dimensions.get('window');
const SIDEBAR_WIDTH = width * 0.80;

// ================= مكونات Markdown المخصصة =================

// --- تحليل النص المضمن (Bold, Italic, Code, Links, Strikethrough, Images) ---
function parseInlineTokens(text) {
  const regex = /(`[^`]+`|~~[^~]+~~|\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_|!\[([^\]]+)\]\(([^)]+)\)|\[([^\]]+)\]\(([^)]+)\))/g;
  let parts = [];
  let lastIndex = 0;
  let match;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.substring(lastIndex, match.index) });
    }
    const full = match[0];
    if (full.startsWith('`') && full.endsWith('`')) {
      parts.push({ type: 'code', content: full.slice(1, -1) });
    } else if (full.startsWith('~~') && full.endsWith('~~')) {
      parts.push({ type: 'strikethrough', content: full.slice(2, -2) });
    } else if (full.startsWith('**') && full.endsWith('**')) {
      parts.push({ type: 'bold', content: full.slice(2, -2) });
    } else if (full.startsWith('__') && full.endsWith('__')) {
      parts.push({ type: 'bold', content: full.slice(2, -2) });
    } else if (full.startsWith('*') && full.endsWith('*')) {
      parts.push({ type: 'italic', content: full.slice(1, -1) });
    } else if (full.startsWith('_') && full.endsWith('_')) {
      parts.push({ type: 'italic', content: full.slice(1, -1) });
    } else if (full.startsWith('![')) {
      const imageMatch = full.match(/^!\[([^\]]+)\]\(([^)]+)\)$/);
      if (imageMatch) {
        parts.push({ type: 'image', alt: imageMatch[1], url: imageMatch[2] });
      } else {
        parts.push({ type: 'text', content: full });
      }
    } else if (full.startsWith('[')) {
      const linkMatch = full.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        parts.push({ type: 'link', content: linkMatch[1], url: linkMatch[2] });
      } else {
        parts.push({ type: 'text', content: full });
      }
    }
    lastIndex = match.index + full.length;
  }
  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.substring(lastIndex) });
  }
  return parts;
}

// --- مكون النص المضمن القابل للتنسيق ---
function InlineText({ tokens, baseStyle }) {
  return (
    <Text style={baseStyle}>
      {tokens.map((token, idx) => {
        switch (token.type) {
          case 'text':
            return <Text key={idx}>{token.content}</Text>;
          case 'bold':
            return <Text key={idx} style={{ fontWeight: 'bold' }}>{token.content}</Text>;
          case 'italic':
            return <Text key={idx} style={{ fontStyle: 'italic' }}>{token.content}</Text>;
          case 'strikethrough':
            return <Text key={idx} style={{ textDecorationLine: 'line-through' }}>{token.content}</Text>;
          case 'code':
            return (
              <Text key={idx} style={styles.inlineCode}>
                {token.content}
              </Text>
            );
          case 'link':
            return (
              <Text
                key={idx}
                style={{ color: '#ffffff', textDecorationLine: 'underline', fontWeight: '600' }}
                onPress={() => Linking.openURL(token.url)}
              >
                {token.content}
              </Text>
            );
          case 'image':
            return (
              <Image
                key={idx}
                source={{ uri: token.url }}
                style={styles.markdownImage}
                resizeMode="contain"
                onError={() => console.warn('Failed to load image:', token.url)}
              />
            );
          default:
            return <Text key={idx}>{token.content}</Text>;
        }
      })}
    </Text>
  );
}

// --- مكون كتلة الكود (موجود مسبقاً) ---
function CodeBlock({ code, language, fontSize = 16 }) {
  const [copied, setCopied] = useState(false);

  const copyCode = async () => {
    await Clipboard.setStringAsync(code || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <View style={styles.codeBlockWrapper}>
      <View style={styles.codeBlockHeader}>
        <Text style={styles.codeLanguage}>{language || 'code'}</Text>
        <TouchableOpacity style={styles.codeCopyButton} onPress={copyCode}>
          <Ionicons name={copied ? 'checkmark-outline' : 'copy-outline'} size={14} color="#ffffff" />
          <Text style={styles.codeCopyButtonText}>{copied ? 'تم النسخ' : 'نسخ'}</Text>
        </TouchableOpacity>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.codeScroll}>
        <Text style={[styles.codeBlockText, { fontSize: Math.max(fontSize - 3, 10), lineHeight: Math.max(fontSize - 3, 10) + 7 }]}>
          {code}
        </Text>
      </ScrollView>
    </View>
  );
}

// --- تحليل ماركداون إلى كتل (Paragraphs, Headings, Lists, Code, Tables, HR, etc.) ---
function parseMarkdownBlocks(text) {
  const lines = text.split('\n');
  const blocks = [];
  let i = 0;

  const isBlockStart = (line) => {
    return (
      line.startsWith('#') ||
      line.startsWith('```') ||
      line.startsWith('> ') ||
      line.startsWith('|') || // جداول
      /^[\-\*\+]\s/.test(line) ||
      /^\d+\.\s/.test(line) ||
      /^(\-{3,}|\*{3,}|_{3,})\s*$/.test(line) // خط أفقي
    );
  };

  while (i < lines.length) {
    const line = lines[i];

    // خط أفقي
    if (/^(\-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ type: 'hr' });
      i++;
      continue;
    }

    // كود محاط بـ ```
    if (line.startsWith('```')) {
      const language = line.substring(3).trim();
      let code = '';
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) {
        code += (code ? '\n' : '') + lines[i];
        i++;
      }
      i++; // تجاوز سطر الإغلاق ```
      blocks.push({ type: 'code', language, content: code });
      continue;
    }

    // العناوين
    const headingMatch = line.match(/^(#{1,6})\s+(.*)/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const content = headingMatch[2];
      blocks.push({ type: 'heading', level, content });
      i++;
      continue;
    }

    // اقتباس
    if (line.startsWith('> ')) {
      const content = line.substring(2);
      blocks.push({ type: 'blockquote', content });
      i++;
      continue;
    }

    // جداول
    if (line.startsWith('|')) {
      const tableLines = [];
      while (i < lines.length && lines[i].startsWith('|')) {
        tableLines.push(lines[i]);
        i++;
      }
      if (tableLines.length >= 2) {
        // أول سطر: الرؤوس، ثاني سطر: الفاصل (نتجاهله)، باقي الأسطر: بيانات
        const headerLine = tableLines[0];
        const headers = headerLine.split('|').filter(cell => cell.trim() !== '').map(cell => cell.trim());
        const dataLines = tableLines.slice(2);
        const rows = dataLines.map(line => {
          const cells = line.split('|').filter(cell => cell.trim() !== '').map(cell => cell.trim());
          // عدد الخلايا قد يختلف، نأخذ أول n خلية بعدد الرؤوس
          return cells.slice(0, headers.length);
        });
        blocks.push({ type: 'table', headers, rows });
      } else {
        // جدول غير مكتمل، تجاهله
      }
      continue;
    }

    // قائمة غير مرتبة
    if (/^[\-\*\+]\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^[\-\*\+]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[\-\*\+]\s/, ''));
        i++;
      }
      blocks.push({ type: 'unordered_list', items });
      continue;
    }

    // قائمة مرتبة
    if (/^\d+\.\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s/, ''));
        i++;
      }
      blocks.push({ type: 'ordered_list', items });
      continue;
    }

    // سطر فارغ
    if (line.trim() === '') {
      i++;
      continue;
    }

    // فقرة (تجميع الأسطر المتتالية حتى سطر فارغ أو بداية كتلة أخرى)
    let paragraphLines = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
      paragraphLines.push(lines[i]);
      i++;
    }
    blocks.push({ type: 'paragraph', content: paragraphLines.join('\n') });
  }

  return blocks;
}

// --- مكون عرض ماركداون المخصص بالكامل ---
function MarkdownText({ text, style, fontSize = 16 }) {
  if (!text) return null;

  // تنظيف النص من الكلمات غير المرغوبة
  const cleanText = text
    .replace(/FINISHEDSEARCH/gi, '')
    .replace(/FINISHED/gi, '')
    .trim();

  const blocks = parseMarkdownBlocks(cleanText);
  const baseStyle = {
    color: '#ffffff',
    textAlign: 'right',
    writingDirection: 'rtl',
    fontSize: fontSize,
    lineHeight: fontSize + 8,
  };

  return (
    <View style={style}>
      {blocks.map((block, index) => {
        switch (block.type) {
          case 'heading': {
            const headingSizes = {
              1: fontSize + 10,
              2: fontSize + 7,
              3: fontSize + 4,
              4: fontSize + 2,
              5: fontSize + 1,
              6: fontSize,
            };
            const level = Math.min(block.level, 6);
            const headingStyle = {
              ...baseStyle,
              fontSize: headingSizes[level],
              fontWeight: level <= 3 ? '800' : '700',
              marginBottom: 8,
            };
            return (
              <InlineText
                key={index}
                tokens={parseInlineTokens(block.content)}
                baseStyle={headingStyle}
              />
            );
          }

          case 'paragraph':
            return (
              <InlineText
                key={index}
                tokens={parseInlineTokens(block.content)}
                baseStyle={{ ...baseStyle, marginBottom: 8 }}
              />
            );

          case 'code':
            return (
              <CodeBlock
                key={index}
                code={block.content}
                language={block.language}
                fontSize={fontSize}
              />
            );

          case 'unordered_list':
            return (
              <View key={index} style={{ marginBottom: 8 }}>
                {block.items.map((item, i) => (
                  <View key={i} style={styles.listItemRow}>
                    <Text style={styles.bulletPoint}>{'•'}</Text>
                    <View style={{ flex: 1 }}>
                      <InlineText
                        tokens={parseInlineTokens(item)}
                        baseStyle={baseStyle}
                      />
                    </View>
                  </View>
                ))}
              </View>
            );

          case 'ordered_list':
            return (
              <View key={index} style={{ marginBottom: 8 }}>
                {block.items.map((item, i) => (
                  <View key={i} style={styles.listItemRow}>
                    <Text style={styles.bulletPoint}>{`${i + 1}.`}</Text>
                    <View style={{ flex: 1 }}>
                      <InlineText
                        tokens={parseInlineTokens(item)}
                        baseStyle={baseStyle}
                      />
                    </View>
                  </View>
                ))}
              </View>
            );

          case 'blockquote':
            return (
              <View key={index} style={styles.blockquote}>
                <InlineText
                  tokens={parseInlineTokens(block.content)}
                  baseStyle={baseStyle}
                />
              </View>
            );

          case 'hr':
            return <View key={index} style={styles.hr} />;

          case 'table':
            return (
              <View key={index} style={styles.tableContainer}>
                <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                  <View>
                    {/* صف الرؤوس */}
                    <View style={styles.tableRow}>
                      {block.headers.map((header, colIdx) => (
                        <View key={colIdx} style={[styles.tableCell, styles.tableHeaderCell]}>
                          <InlineText
                            tokens={parseInlineTokens(header)}
                            baseStyle={{ ...baseStyle, fontWeight: 'bold', textAlign: 'right' }}
                          />
                        </View>
                      ))}
                    </View>
                    {/* صفوف البيانات */}
                    {block.rows.map((row, rowIdx) => (
                      <View key={rowIdx} style={styles.tableRow}>
                        {row.map((cell, colIdx) => (
                          <View key={colIdx} style={styles.tableCell}>
                            <InlineText
                              tokens={parseInlineTokens(cell)}
                              baseStyle={{ ...baseStyle, textAlign: 'right' }}
                            />
                          </View>
                        ))}
                      </View>
                    ))}
                  </View>
                </ScrollView>
              </View>
            );

          default:
            return null;
        }
      })}
    </View>
  );
}

// ================= مكون الرسالة القابل للطي (مع دعم التفكير) =================
function CollapsibleMessage({ item, isStreaming, isLoading, hasSources, openSourcesModal, bubbleFontSize, longMessageCollapseEnabled, longMessageCollapseTarget, onOpenMessageModal, onRegenerateMessage }) {
  const [expanded, setExpanded] = useState(false);
  const [messageCopied, setMessageCopied] = useState(false);
  const [thinkingVisible, setThinkingVisible] = useState(true); // مفتوح افتراضياً أثناء التدفق

  const copyFullMessage = async () => {
    await Clipboard.setStringAsync(item.text || '');
    setMessageCopied(true);
    setTimeout(() => setMessageCopied(false), 1400);
  };
  const shouldApplyCollapse = longMessageCollapseEnabled && (
    longMessageCollapseTarget === 'all' ||
    longMessageCollapseTarget === item.sender ||
    (longMessageCollapseTarget === 'user' && item.sender === 'user') ||
    (longMessageCollapseTarget === 'ai' && item.sender === 'ai')
  );
  const isLongMessage = item.text && item.text.split(/\r\n|\r|\n/).length > 4;
  const shouldCollapse = shouldApplyCollapse && isLongMessage && !expanded;

  // إذا انتهى التفكير (لم يعد هناك تدفق) نغلق الصندوق تلقائياً
  useEffect(() => {
    if (!isStreaming && item.thinkingText) {
      setThinkingVisible(false);
    }
  }, [isStreaming, item.thinkingText]);

  return (
    <View style={{ flex: 1, alignItems: item.sender === 'user' ? 'flex-start' : 'flex-end' }}>
      {/* صندوق التفكير */}
      {item.sender === 'ai' && item.thinkingText ? (
        <View style={styles.thinkingContainer}>
          <TouchableOpacity
            style={styles.thinkingHeader}
            onPress={() => setThinkingVisible(!thinkingVisible)}
          >
            <Ionicons name="bulb-outline" size={16} color="#aaa" />
            <Text style={styles.thinkingHeaderText}>التفكير</Text>
            <Ionicons name={thinkingVisible ? 'chevron-up' : 'chevron-down'} size={16} color="#aaa" />
          </TouchableOpacity>
          {thinkingVisible && (
            <View style={styles.thinkingBody}>
              <MarkdownText text={item.thinkingText} fontSize={bubbleFontSize - 2} style={styles.thinkingText} />
            </View>
          )}
        </View>
      ) : null}

      <TouchableOpacity
        activeOpacity={0.92}
        onLongPress={() => onOpenMessageModal(item)}
        delayLongPress={350}
        style={[styles.bubble, item.sender === 'user' ? styles.userBubble : styles.aiBubble]}
      >
        {item.sender === 'ai' ? (
          <View style={shouldCollapse ? styles.collapsedMessageContent : null}>
            <MarkdownText text={item.text} style={styles.messageText} fontSize={bubbleFontSize} />
          </View>
        ) : (
          <Text
            style={[styles.messageText, { color: '#ffffff', fontSize: bubbleFontSize, lineHeight: bubbleFontSize + 8 }]}
            numberOfLines={shouldCollapse ? 4 : undefined}
          >
            {item.text}
          </Text>
        )}
        {shouldCollapse && (
          <TouchableOpacity style={styles.expandMessageButton} onPress={() => setExpanded(true)}>
            <Text style={styles.expandMessageButtonText}>عرض الرسالة كاملة</Text>
          </TouchableOpacity>
        )}
        {expanded && shouldApplyCollapse && isLongMessage && (
          <TouchableOpacity style={styles.expandMessageButton} onPress={() => setExpanded(false)}>
            <Text style={styles.expandMessageButtonText}>إخفاء الرسالة</Text>
          </TouchableOpacity>
        )}
        {isStreaming && isLoading && item.text.length === 0 && !item.thinkingText && (
          <View style={styles.typingIndicator}>
            <ActivityIndicator size="small" color="#ffffff" />
          </View>
        )}
      </TouchableOpacity>
      <View style={[styles.messageActions, item.sender === 'user' ? styles.userMessageActions : styles.aiMessageActions]}>
        {item.sender !== 'user' && (
          <TouchableOpacity style={styles.messageActionButton} onPress={copyFullMessage}>
            <Ionicons name={messageCopied ? 'checkmark-outline' : 'copy-outline'} size={18} color="#aaaaaa" />
          </TouchableOpacity>
        )}
        {item.sender === 'ai' && (
          <TouchableOpacity style={styles.messageActionButton} onPress={() => onRegenerateMessage(item.id)} disabled={isLoading}>
            <Ionicons name="refresh-outline" size={18} color="#aaaaaa" />
          </TouchableOpacity>
        )}
      </View>
      {hasSources && (
        <TouchableOpacity
          style={styles.sourcesButton}
          onPress={() => openSourcesModal(item.sources)}
        >
          <Ionicons name="planet-outline" size={14} color="#ffffff" />
          <Text style={styles.sourcesButtonText}>
            {item.sources.length} مصادر بحث
          </Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

// ================= التطبيق الرئيسي =================
export default function App() {
  const [chats, setChats] = useState([
    { id: 'default', title: 'محادثة جديدة', session_id: null, messages: [], parent_message_id: null, request_message_id: null, pinned: false }
  ]);
  const [currentChatId, setCurrentChatId] = useState('default');
  const [inputText, setInputText] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState(null);
  const [streamingText, setStreamingText] = useState('');
  const [sourcesModalVisible, setSourcesModalVisible] = useState(false);
  const [settingsModalVisible, setSettingsModalVisible] = useState(false);
  const [selectedSources, setSelectedSources] = useState([]);
  const [bubbleFontSize, setBubbleFontSize] = useState(16);
  const [longMessageCollapseEnabled, setLongMessageCollapseEnabled] = useState(true);
  const [longMessageCollapseTarget, setLongMessageCollapseTarget] = useState('user');
  const [selectedMessage, setSelectedMessage] = useState(null);
  const [messageModalVisible, setMessageModalVisible] = useState(false);
  const [messageModalCopied, setMessageModalCopied] = useState(false);
  const [renameModalVisible, setRenameModalVisible] = useState(false);
  const [chatToRename, setChatToRename] = useState(null);
  const [renameText, setRenameText] = useState('');

  const [pendingFiles, setPendingFiles] = useState([]);
  const [fullImageUri, setFullImageUri] = useState(null);

  // حالات الأوضاع العامة
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [globalSearchEnabled, setGlobalSearchEnabled] = useState(true);

  const sidebarAnim = useRef(new Animated.Value(SIDEBAR_WIDTH)).current;
  const flatListRef = useRef();
  const fadeAnim = useRef(new Animated.Value(1)).current;
  const sendButtonScale = useRef(new Animated.Value(1)).current;

  // حالات التمرير الذكي
  const [isNearBottom, setIsNearBottom] = useState(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const currentChat = chats.find(c => c.id === currentChatId) || chats[0];
  const sortedChats = [...chats].sort((a, b) => (b.pinned === true) - (a.pinned === true));

  // ================= استعادة وحفظ البيانات تلقائياً =================
  useEffect(() => {
    loadChatsFromStorage();
  }, []);

  useEffect(() => {
    saveChatsToStorage(chats);
  }, [chats]);

  useEffect(() => {
    saveSettingsToStorage();
  }, [bubbleFontSize, longMessageCollapseEnabled, longMessageCollapseTarget, thinkingEnabled, globalSearchEnabled]);

  const loadChatsFromStorage = async () => {
    try {
      const savedChats = await AsyncStorage.getItem('@deepseek_premium_chats');
      const savedChatId = await AsyncStorage.getItem('@deepseek_premium_current_id');
      const savedBubbleFontSize = await AsyncStorage.getItem('@deepseek_premium_bubble_font_size');
      const savedLongMessageCollapseEnabled = await AsyncStorage.getItem('@deepseek_premium_long_collapse_enabled');
      const savedLongMessageCollapseTarget = await AsyncStorage.getItem('@deepseek_premium_long_collapse_target');
      const savedThinkingEnabled = await AsyncStorage.getItem('@deepseek_premium_thinking_enabled');
      const savedSearchEnabled = await AsyncStorage.getItem('@deepseek_premium_search_enabled');

      if (savedBubbleFontSize) {
        setBubbleFontSize(Number(savedBubbleFontSize));
      }
      if (savedLongMessageCollapseEnabled !== null) {
        setLongMessageCollapseEnabled(savedLongMessageCollapseEnabled === 'true');
      }
      if (savedLongMessageCollapseTarget) {
        setLongMessageCollapseTarget(savedLongMessageCollapseTarget);
      }
      if (savedThinkingEnabled !== null) {
        setThinkingEnabled(savedThinkingEnabled === 'true');
      }
      if (savedSearchEnabled !== null) {
        setGlobalSearchEnabled(savedSearchEnabled === 'true');
      }

      if (savedChats) {
        const parsedChats = JSON.parse(savedChats).map(chat => ({
          pinned: false,
          request_message_id: null,
          ...chat,
        }));
        setChats(parsedChats);
        if (savedChatId && parsedChats.some(c => c.id === savedChatId)) {
          setCurrentChatId(savedChatId);
        } else if (parsedChats.length > 0) {
          setCurrentChatId(parsedChats[0].id);
        }
      }
    } catch (e) {
      console.error("⚠️ فشل تحميل المحادثات المستمرة:", e);
    }
  };

  const saveChatsToStorage = async (currentChats) => {
    try {
      await AsyncStorage.setItem('@deepseek_premium_chats', JSON.stringify(currentChats));
      await AsyncStorage.setItem('@deepseek_premium_current_id', currentChatId);
    } catch (e) {
      console.error("⚠️ فشل حفظ المحادثات تلقائياً:", e);
    }
  };

  const saveSettingsToStorage = async () => {
    try {
      await AsyncStorage.setItem('@deepseek_premium_bubble_font_size', String(bubbleFontSize));
      await AsyncStorage.setItem('@deepseek_premium_long_collapse_enabled', String(longMessageCollapseEnabled));
      await AsyncStorage.setItem('@deepseek_premium_long_collapse_target', longMessageCollapseTarget);
      await AsyncStorage.setItem('@deepseek_premium_thinking_enabled', String(thinkingEnabled));
      await AsyncStorage.setItem('@deepseek_premium_search_enabled', String(globalSearchEnabled));
    } catch (e) {
      console.error("⚠️ فشل حفظ الإعدادات:", e);
    }
  };

  const clearAllChats = async () => {
    try {
      const defaultChat = [{ id: 'default', title: 'محادثة جديدة', session_id: null, messages: [], parent_message_id: null, request_message_id: null, pinned: false }];
      setChats(defaultChat);
      setCurrentChatId('default');
      await AsyncStorage.setItem('@deepseek_premium_chats', JSON.stringify(defaultChat));
      await AsyncStorage.setItem('@deepseek_premium_current_id', 'default');
      setSettingsModalVisible(false);
    } catch (e) {
      console.error("⚠️ فشل مسح البيانات:", e);
    }
  };

  // --- دوال الرؤوس والمعرفات ---
  function generateDeviceId() {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    let result = '';
    for (let i = 0; i < 88; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
  }

  function generateRangersId() {
    const ts = BigInt(Date.now());
    const rv = BigInt(Math.floor(1000000000 + Math.random() * 8999999999));
    return ((ts << 32n) | rv).toString();
  }

  function getTzOffset() {
    return (new Date().getTimezoneOffset() * -60).toString();
  }

  function buildFullHeaders(powResponse) {
    return {
      'User-Agent': 'DeepSeek/2.1.1 Android/36',
      'Accept': 'application/json',
      'Accept-Encoding': 'gzip',
      'Content-Type': 'application/json',
      'x-client-platform': 'android',
      'x-client-version': '2.1.1',
      'x-client-locale': 'ar',
      'x-client-bundle-id': 'com.deepseek.chat',
      'x-rangers-id': generateRangersId(),
      'x-client-timezone-offset': getTzOffset(),
      'x-device-id': generateDeviceId(),
      'x-os-version': '30',
      'x-app-version': '2.1.1',
      'Authorization': `Bearer ${TOKEN}`,
      'X-DS-PoW-Response': powResponse,
      'accept-charset': 'UTF-8',
    };
  }

  async function getFreshPow() {
    try {
      const response = await fetch(POW_API_URL);
      if (response.status !== 200) {
        throw new Error(`فشل الحصول على POW: ${response.status}`);
      }
      const data = await response.json();
      if (!data.pow_response || !data.solved_json) {
        throw new Error(`استجابة POW غير مكتملة: ${JSON.stringify(data)}`);
      }
      return {
        powResponse: data.pow_response,
        powData: data.solved_json
      };
    } catch (error) {
      console.error("⚠️ تعذر الحصول على POW جديد:", error);
      throw error;
    }
  }

  async function createChatSession() {
    const url = "https://chat.deepseek.com/api/v0/chat_session/create";
    const headers = {
      'x-client-bundle-id': 'com.deepseek.chat',
      'x-client-platform': 'web',
      'x-client-version': '2.0.0',
      'x-client-locale': 'en_US',
      'x-client-timezone-offset': getTzOffset(),
      'x-app-version': '2.0.0',
      'Authorization': `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
      'Accept': '*/*'
    };
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({})
      });
      const data = await response.json();
      return data.data.biz_data.chat_session.id;
    } catch (error) {
      console.error("⚠️ خطأ عند إنشاء الجلسة:", error);
      throw error;
    }
  }

  // الدالة الرئيسية للإرسال مع دعم parent_message_id و ref_file_ids و thinking_enabled و THINKING chunks
  async function askDeepseekStream({
    prompt,
    sessionId,
    parentMessageId,
    onChunk,
    onFirstResponse,
    onSearchResults,
    onDone,
    onError,
    refFileIds = [],
    searchEnabled = true,
    thinkingEnabled = false,
  }) {
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      activeSessionId = await createChatSession();
    }

    const { powResponse, powData } = await getFreshPow();
    const headers = buildFullHeaders(powResponse);

    const payload = {
      chat_session_id: activeSessionId,
      parent_message_id: parentMessageId,
      prompt: prompt,
      ref_file_ids: refFileIds,
      thinking_enabled: thinkingEnabled,
      search_enabled: searchEnabled,
      action: null,
      preempt: false,
      pow: powData,
      stream: true
    };

    const url = "https://chat.deepseek.com/api/v0/chat/completion";

    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', 'application/json');
    Object.keys(headers).forEach(key => xhr.setRequestHeader(key, headers[key]));

    let lastProcessedIndex = 0;
    let accumulatedText = '';
    let firstResponseHandled = false;
    let currentRequestMsgId = null;
    let currentResponseMsgId = null;
    let searchResults = [];
    let searchHandled = false;
    let thinkingAccumulated = '';

    xhr.onprogress = () => {
      const responseText = xhr.responseText;
      const newPart = responseText.substring(lastProcessedIndex);
      lastProcessedIndex = responseText.length;

      const lines = newPart.split('\n');
      for (let line of lines) {
        line = line.trim();

        if (!firstResponseHandled && line.startsWith("data: ")) {
          const rawData = line.substring(6);
          try {
            const parsed = JSON.parse(rawData);
            if (parsed.request_message_id && parsed.response_message_id) {
              currentRequestMsgId = parsed.request_message_id;
              currentResponseMsgId = parsed.response_message_id;
              firstResponseHandled = true;
              if (onFirstResponse) {
                onFirstResponse({
                  requestMessageId: currentRequestMsgId,
                  responseMessageId: currentResponseMsgId,
                  sessionId: activeSessionId
                });
              }
              continue;
            }
          } catch (e) {}
        }

        if (!searchHandled && line.startsWith("data: ")) {
          const rawChunk = line.substring(6);
          try {
            const item = JSON.parse(rawChunk);
            if (item && item.v && typeof item.v === 'object') {
              if (item.v.response && item.v.response.fragments) {
                for (let frag of item.v.response.fragments) {
                  if (frag.type === "SEARCH" && frag.results && frag.results.length > 0) {
                    searchResults = frag.results.map(r => ({
                      url: r.url,
                      title: r.title,
                      snippet: r.snippet,
                      site_name: r.site_name,
                      site_icon: r.site_icon
                    }));
                    searchHandled = true;
                    if (onSearchResults) {
                      onSearchResults(searchResults);
                    }
                  } else if (frag.type === "THINKING") {
                    thinkingAccumulated += frag.content || '';
                    // استدعاء callback خاص بالتفكير إذا وجد
                    if (onThinkingChunk) onThinkingChunk(thinkingAccumulated);
                  } else if (frag.type === "RESPONSE") {
                    accumulatedText += frag.content || "";
                  }
                }
              }
            }
          } catch (err) {}
        }

        if (line.startsWith("data: ")) {
          const rawChunk = line.substring(6);
          try {
            const item = JSON.parse(rawChunk);
            if (item && item.v) {
              if (typeof item.v === 'string') {
                accumulatedText += item.v;
              } else if (typeof item.v === 'object' && item.v.response && item.v.response.fragments) {
                for (let frag of item.v.response.fragments) {
                  if (frag.type === "THINKING") {
                    thinkingAccumulated += frag.content || '';
                    if (onThinkingChunk) onThinkingChunk(thinkingAccumulated);
                  } else if (frag.type === "RESPONSE") {
                    accumulatedText += frag.content || "";
                  }
                }
              }
            }
            if (item && item.p === "response/fragments/-1/content" && item.o === "APPEND" && item.v) {
              accumulatedText += item.v;
            }
          } catch (err) {}
        }
      }
      if (accumulatedText.length > 0) {
        onChunk(accumulatedText);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const remaining = xhr.responseText.substring(lastProcessedIndex);
        const lines = remaining.split('\n');
        for (let line of lines) {
          line = line.trim();

          if (!searchHandled && line.startsWith("data: ")) {
            const rawChunk = line.substring(6);
            try {
              const item = JSON.parse(rawChunk);
              if (item && item.v && typeof item.v === 'object') {
                if (item.v.response && item.v.response.fragments) {
                  for (let frag of item.v.response.fragments) {
                    if (frag.type === "SEARCH" && frag.results && frag.results.length > 0) {
                      searchResults = frag.results.map(r => ({
                        url: r.url,
                        title: r.title,
                        snippet: r.snippet,
                        site_name: r.site_name,
                        site_icon: r.site_icon
                      }));
                      searchHandled = true;
                      if (onSearchResults) {
                        onSearchResults(searchResults);
                      }
                    } else if (frag.type === "THINKING") {
                      thinkingAccumulated += frag.content || '';
                      if (onThinkingChunk) onThinkingChunk(thinkingAccumulated);
                    } else if (frag.type === "RESPONSE") {
                      accumulatedText += frag.content || "";
                    }
                  }
                }
              }
            } catch (err) {}
          }

          if (line.startsWith("data: ")) {
            const rawChunk = line.substring(6);
            try {
              const item = JSON.parse(rawChunk);
              if (item && item.v) {
                if (typeof item.v === 'string') {
                  accumulatedText += item.v;
                } else if (typeof item.v === 'object' && item.v.response && item.v.response.fragments) {
                  for (let frag of item.v.response.fragments) {
                    if (frag.type === "THINKING") {
                      thinkingAccumulated += frag.content || '';
                      if (onThinkingChunk) onThinkingChunk(thinkingAccumulated);
                    } else if (frag.type === "RESPONSE") {
                      accumulatedText += frag.content || "";
                    }
                  }
                }
              }
              if (item && item.p === "response/fragments/-1/content" && item.o === "APPEND" && item.v) {
                accumulatedText += item.v;
              }
            } catch (e) {}
          }
        }
        onDone({
          text: accumulatedText,
          sessionId: activeSessionId,
          requestMessageId: currentRequestMsgId,
          responseMessageId: currentResponseMsgId,
          searchResults: searchResults,
          thinkingText: thinkingAccumulated // إرجاع نص التفكير النهائي
        });
      } else {
        onError(new Error(`خطأ ${xhr.status}: ${xhr.responseText}`));
      }
    };

    xhr.onerror = () => {
      onError(new Error('فشل الاتصال بالخادم'));
    };

    xhr.send(JSON.stringify(payload));
  }

  // --- رفع ملف إلى الخادم عبر Railway ---
  const uploadFileAndGetId = async (fileUri, fileName) => {
    try {
      const formData = new FormData();
      formData.append('file', {
        uri: fileUri,
        name: fileName || 'file',
        type: 'application/octet-stream',
      });

      const response = await fetch(`${RAILWAY_SERVER_URL}/upload`, {
        method: 'POST',
        body: formData,
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      if (!response.ok) {
        throw new Error(`Upload failed: ${response.status}`);
      }

      const data = await response.json();
      return data.file_id;
    } catch (error) {
      Alert.alert('خطأ في رفع الملف', error.message);
      return null;
    }
  };

  // --- اختيار ملفات أو صور من الجهاز ورفعها ---
  const pickAndUploadFile = () => {
    Alert.alert(
      'إرفاق ملف',
      'اختر نوع الملف الذي تريد إرفاقه',
      [
        {
          text: 'ملف (مستندات، PDF، إلخ)',
          onPress: async () => {
            try {
              const result = await DocumentPicker.getDocumentAsync({
                type: '*/*',
                copyToCacheDirectory: true,
                multiple: true,
              });
              if (result.canceled) return;
              const files = result.assets || [result];
              for (const file of files) {
                const fileUri = file.uri;
                const fileName = file.name;
                const fileId = await uploadFileAndGetId(fileUri, fileName);
                if (fileId) {
                  setPendingFiles(prev => [...prev, {
                    id: Date.now().toString() + Math.random(),
                    name: fileName,
                    fileId,
                    uri: fileUri,
                    type: 'file',
                  }]);
                }
              }
            } catch (err) {
              console.log(err);
            }
          },
        },
        {
          text: 'صورة',
          onPress: async () => {
            const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
            if (status !== 'granted') {
              Alert.alert('صلاحية مرفوضة', 'نحتاج صلاحية الوصول للصور لرفعها.');
              return;
            }
            const result = await ImagePicker.launchImageLibraryAsync({
              mediaTypes: ImagePicker.MediaTypeOptions.Images,
              allowsMultipleSelection: true,
              quality: 1,
            });
            if (result.canceled) return;
            const assets = result.assets;
            for (const asset of assets) {
              const fileUri = asset.uri;
              const fileName = asset.fileName || `image_${Date.now()}.jpg`;
              const fileId = await uploadFileAndGetId(fileUri, fileName);
              if (fileId) {
                setPendingFiles(prev => [...prev, {
                  id: Date.now().toString() + Math.random(),
                  name: fileName,
                  fileId,
                  uri: fileUri,
                  type: 'image',
                }]);
              }
            }
          },
        },
        { text: 'إلغاء', style: 'cancel' },
      ],
    );
  };

  // إزالة ملف من قائمة المرفقات
  const removeFile = (id) => {
    setPendingFiles(prev => prev.filter(f => f.id !== id));
  };

  // فتح الصورة بالحجم الكامل
  const openFullImage = (uri) => {
    setFullImageUri(uri);
  };

  // --- إرسال الرسالة من الواجهة ---
  async function handleSendMessage() {
    if (!inputText.trim() && pendingFiles.length === 0) return;

    const userMessageText = inputText.trim();
    setInputText('');
    setIsLoading(true);

    const userMessageId = Date.now().toString() + '-user';
    const userMessage = {
      id: userMessageId,
      sender: 'user',
      text: userMessageText,
      attachments: pendingFiles.length > 0 ? [...pendingFiles] : undefined,
    };

    const updatedMessages = [...currentChat.messages, userMessage];

    setChats(prev =>
      prev.map(c =>
        c.id === currentChatId
          ? {
              ...c,
              messages: updatedMessages,
              title: currentChat.title === 'محادثة جديدة' ? userMessageText.substring(0, 20) : c.title
            }
          : c
      )
    );

    const tempAiId = Date.now().toString() + '-ai';
    const aiPlaceholder = {
      id: tempAiId,
      sender: 'ai',
      text: '',
      sources: [],
      thinkingText: null,
    };
    const messagesWithPlaceholder = [...updatedMessages, aiPlaceholder];
    setChats(prev =>
      prev.map(c =>
        c.id === currentChatId
          ? { ...c, messages: messagesWithPlaceholder }
          : c
      )
    );
    setStreamingMessageId(tempAiId);
    setStreamingText('');

    // تجهيز معرّفات الملفات
    const refFileIds = pendingFiles.map(f => f.fileId);
    // البحث يكون حسب الإعداد العام (لا تعطيل)
    const effectiveSearchEnabled = globalSearchEnabled;
    // مسح الملفات بعد الإرسال
    setPendingFiles([]);

    // دالة معالجة تدفق التفكير
    const onThinkingChunk = (thinkingText) => {
      setChats(prev =>
        prev.map(c =>
          c.id === currentChatId
            ? {
                ...c,
                messages: c.messages.map(m =>
                  m.id === tempAiId ? { ...m, thinkingText } : m
                )
              }
            : c
        )
      );
    };

    try {
      await askDeepseekStream({
        prompt: userMessageText,
        sessionId: currentChat.session_id,
        parentMessageId: currentChat.parent_message_id,
        refFileIds: refFileIds,
        searchEnabled: effectiveSearchEnabled,
        thinkingEnabled: thinkingEnabled,
        onThinkingChunk: onThinkingChunk,
        onChunk: (chunkText) => {
          setStreamingText(chunkText);
          setChats(prev =>
            prev.map(c =>
              c.id === currentChatId
                ? {
                    ...c,
                    messages: c.messages.map(m =>
                      m.id === tempAiId ? { ...m, text: chunkText } : m
                    )
                  }
                : c
            )
          );
        },
        onFirstResponse: ({ requestMessageId, responseMessageId, sessionId }) => {
          setChats(prev =>
            prev.map(c =>
              c.id === currentChatId
                ? {
                    ...c,
                    session_id: sessionId,
                    parent_message_id: responseMessageId,
                  }
                : c
            )
          );
        },
        onSearchResults: (sources) => {
          setChats(prev =>
            prev.map(c =>
              c.id === currentChatId
                ? {
                    ...c,
                    messages: c.messages.map(m =>
                      m.id === tempAiId ? { ...m, sources: sources } : m
                    )
                  }
                : c
            )
          );
        },
        onDone: ({ text, sessionId, responseMessageId, searchResults, thinkingText }) => {
          setChats(prev =>
            prev.map(c =>
              c.id === currentChatId
                ? {
                    ...c,
                    session_id: sessionId,
                    parent_message_id: responseMessageId,
                    messages: c.messages.map(m =>
                      m.id === tempAiId ? { ...m, text: text, sources: searchResults || m.sources, thinkingText: thinkingText || m.thinkingText } : m
                    )
                  }
                : c
            )
          );
          setStreamingMessageId(null);
          setIsLoading(false);
          if (isNearBottom) {
            setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
          }
        },
        onError: (error) => {
          const errorMessage = `❌ فشل الاتصال بالخادم: ${error.message}`;
          setChats(prev =>
            prev.map(c =>
              c.id === currentChatId
                ? {
                    ...c,
                    messages: c.messages.map(m =>
                      m.id === tempAiId ? { ...m, text: errorMessage } : m
                    )
                  }
                : c
            )
          );
          setStreamingMessageId(null);
          setIsLoading(false);
        }
      });
    } catch (error) {
      const errorMessage = `❌ فشل الاتصال بالخادم: ${error.message}`;
      setChats(prev =>
        prev.map(c =>
          c.id === currentChatId
            ? {
                ...c,
                messages: c.messages.map(m =>
                  m.id === tempAiId ? { ...m, text: errorMessage } : m
                )
              }
            : c
        )
      );
      setStreamingMessageId(null);
      setIsLoading(false);
    }
  }

  // --- إدارة القائمة الجانبية (من اليمين) ---
  const toggleSidebar = () => {
    if (isSidebarOpen) {
      Animated.timing(sidebarAnim, {
        toValue: SIDEBAR_WIDTH,
        duration: 220,
        useNativeDriver: false,
      }).start(() => setIsSidebarOpen(false));
    } else {
      setIsSidebarOpen(true);
      Animated.timing(sidebarAnim, {
        toValue: 0,
        duration: 220,
        useNativeDriver: false,
      }).start();
    }
  };

  const createNewChat = () => {
    const newId = Date.now().toString();
    const newChatObj = {
      id: newId,
      title: 'محادثة جديدة',
      session_id: null,
      messages: [],
      parent_message_id: null,
      request_message_id: null,
      pinned: false
    };
    setChats(prev => [newChatObj, ...prev]);
    setCurrentChatId(newId);
    if (isSidebarOpen) toggleSidebar();
  };

  const selectChat = (id) => {
    setCurrentChatId(id);
    toggleSidebar();
  };

  const animateSendButton = () => {
    Animated.sequence([
      Animated.timing(sendButtonScale, {
        toValue: 0.85,
        duration: 80,
        useNativeDriver: true,
      }),
      Animated.timing(sendButtonScale, {
        toValue: 1,
        duration: 80,
        useNativeDriver: true,
      }),
    ]).start();
  };

  const openSourcesModal = (sources) => {
    setSelectedSources(sources);
    setSourcesModalVisible(true);
  };

  const openMessageModal = (message) => {
    setSelectedMessage(message);
    setMessageModalCopied(false);
    setMessageModalVisible(true);
  };

  const copySelectedMessage = async () => {
    await Clipboard.setStringAsync(selectedMessage?.text || '');
    setMessageModalCopied(true);
    setTimeout(() => setMessageModalCopied(false), 1400);
  };

  const openRenameChat = (chat) => {
    setChatToRename(chat);
    setRenameText(chat.title || '');
    setRenameModalVisible(true);
  };

  const saveRenamedChat = () => {
    const cleanTitle = renameText.trim();
    if (!chatToRename || !cleanTitle) return;
    setChats(prev => prev.map(chat => chat.id === chatToRename.id ? { ...chat, title: cleanTitle } : chat));
    setRenameModalVisible(false);
    setChatToRename(null);
    setRenameText('');
  };

  const deleteChat = (chatId) => {
    Alert.alert(
      'حذف المحادثة',
      'هل تريد حذف هذه المحادثة نهائياً؟',
      [
        { text: 'إلغاء', style: 'cancel' },
        {
          text: 'حذف',
          style: 'destructive',
          onPress: () => {
            setChats(prev => {
              const remaining = prev.filter(chat => chat.id !== chatId);
              if (remaining.length === 0) {
                const defaultChat = { id: 'default', title: 'محادثة جديدة', session_id: null, messages: [], parent_message_id: null, request_message_id: null, pinned: false };
                setCurrentChatId('default');
                return [defaultChat];
              }
              if (chatId === currentChatId) {
                setCurrentChatId(remaining[0].id);
              }
              return remaining;
            });
          }
        }
      ]
    );
  };

  const togglePinChat = (chatId) => {
    setChats(prev => prev.map(chat => chat.id === chatId ? { ...chat, pinned: !chat.pinned } : chat));
  };

  const regenerateMessage = async (aiMessageId) => {
    if (isLoading) return;
    const targetChat = chats.find(chat => chat.id === currentChatId);
    if (!targetChat) return;
    const aiIndex = targetChat.messages.findIndex(message => message.id === aiMessageId);
    if (aiIndex <= 0) return;
    const previousUserMessage = [...targetChat.messages.slice(0, aiIndex)].reverse().find(message => message.sender === 'user');
    if (!previousUserMessage) return;

    setIsLoading(true);
    setStreamingMessageId(aiMessageId);
    setStreamingText('');
    setChats(prev => prev.map(chat => chat.id === currentChatId ? {
      ...chat,
      messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, text: '', sources: [], thinkingText: null } : message)
    } : chat));

    const onThinkingChunk = (thinkingText) => {
      setChats(prev =>
        prev.map(c =>
          c.id === currentChatId
            ? {
                ...c,
                messages: c.messages.map(m =>
                  m.id === aiMessageId ? { ...m, thinkingText } : m
                )
              }
            : c
        )
      );
    };

    try {
      await askDeepseekStream({
        prompt: previousUserMessage.text,
        sessionId: targetChat.session_id,
        parentMessageId: targetChat.parent_message_id,
        refFileIds: [],
        searchEnabled: globalSearchEnabled,
        thinkingEnabled: thinkingEnabled,
        onThinkingChunk: onThinkingChunk,
        onChunk: (chunkText) => {
          setStreamingText(chunkText);
          setChats(prev => prev.map(chat => chat.id === currentChatId ? {
            ...chat,
            messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, text: chunkText } : message)
          } : chat));
        },
        onFirstResponse: ({ responseMessageId, sessionId }) => {
          setChats(prev => prev.map(chat => chat.id === currentChatId ? { ...chat, session_id: sessionId, parent_message_id: responseMessageId } : chat));
        },
        onSearchResults: (sources) => {
          setChats(prev => prev.map(chat => chat.id === currentChatId ? {
            ...chat,
            messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, sources } : message)
          } : chat));
        },
        onDone: ({ text, sessionId, responseMessageId, searchResults, thinkingText }) => {
          setChats(prev => prev.map(chat => chat.id === currentChatId ? {
            ...chat,
            session_id: sessionId,
            parent_message_id: responseMessageId,
            messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, text, sources: searchResults || message.sources, thinkingText: thinkingText || message.thinkingText } : message)
          } : chat));
          setStreamingMessageId(null);
          setIsLoading(false);
          if (isNearBottom) {
            setTimeout(() => flatListRef.current?.scrollToEnd({ animated: true }), 100);
          }
        },
        onError: (error) => {
          setChats(prev => prev.map(chat => chat.id === currentChatId ? {
            ...chat,
            messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, text: `❌ فشل إعادة التوليد: ${error.message}` } : message)
          } : chat));
          setStreamingMessageId(null);
          setIsLoading(false);
        }
      });
    } catch (error) {
      setChats(prev => prev.map(chat => chat.id === currentChatId ? {
        ...chat,
        messages: chat.messages.map(message => message.id === aiMessageId ? { ...message, text: `❌ فشل إعادة التوليد: ${error.message}` } : message)
      } : chat));
      setStreamingMessageId(null);
      setIsLoading(false);
    }
  };

  // التحكم في التمرير
  const handleScroll = (event) => {
    const offsetY = event.nativeEvent.contentOffset.y;
    const contentHeight = event.nativeEvent.contentSize.height;
    const layoutHeight = event.nativeEvent.layoutMeasurement.height;
    const distanceFromBottom = contentHeight - offsetY - layoutHeight;
    const nearBottom = distanceFromBottom < 100;
    setIsNearBottom(nearBottom);
    setShowScrollToBottom(!nearBottom && contentHeight > layoutHeight * 2);
  };

  const scrollToBottom = () => {
    flatListRef.current?.scrollToEnd({ animated: true });
    setIsNearBottom(true);
    setShowScrollToBottom(false);
  };

  // مكون عرض المرفقات داخل رسالة المستخدم
  const renderMessageAttachments = (attachments) => {
    if (!attachments || attachments.length === 0) return null;
    return (
      <View style={styles.userAttachmentsContainer}>
        {attachments.map(att => (
          att.type === 'image' ? (
            <TouchableOpacity
              key={att.id}
              onPress={() => openFullImage(att.uri)}
              style={styles.userImageAttachment}
            >
              <Image source={{ uri: att.uri }} style={styles.userImageThumb} resizeMode="cover" />
            </TouchableOpacity>
          ) : (
            <View key={att.id} style={styles.userFileAttachment}>
              <Ionicons name="document-outline" size={20} color="#ffffff" />
              <Text style={styles.userFileName} numberOfLines={1}>{att.name}</Text>
            </View>
          )
        ))}
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar barStyle="light-content" backgroundColor="#000000" />

      {/* شريط العناوين العالمي - تصميم نظيف وخفيف */}
      <View style={styles.header}>
        <TouchableOpacity onPress={toggleSidebar} style={styles.iconButton}>
          <Ionicons name="menu-outline" size={26} color="#ffffff" />
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>{currentChat.title}</Text>
        <TouchableOpacity onPress={createNewChat} style={styles.iconButton}>
          <Ionicons name="create-outline" size={24} color="#ffffff" />
        </TouchableOpacity>
      </View>

      {/* منطقة الرسائل الفاخرة */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{ flex: 1 }}
      >
        <FlatList
          ref={flatListRef}
          data={currentChat.messages}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.messagesList}
          onContentSizeChange={() => {
            if (isNearBottom) {
              flatListRef.current?.scrollToEnd({ animated: false });
            }
          }}
          onScroll={handleScroll}
          scrollEventThrottle={100}
          renderItem={({ item }) => {
            const isStreaming = item.id === streamingMessageId;
            const hasSources = item.sender === 'ai' && item.sources && item.sources.length > 0;
            return (
              <View style={[styles.messageRow, item.sender === 'user' ? styles.userRow : styles.aiRow]}>
                {/* عرض المرفقات قبل فقاعة الرسالة للمستخدم */}
                {item.sender === 'user' && renderMessageAttachments(item.attachments)}
                <CollapsibleMessage
                  item={item}
                  isStreaming={isStreaming}
                  isLoading={isLoading}
                  hasSources={hasSources}
                  openSourcesModal={openSourcesModal}
                  bubbleFontSize={bubbleFontSize}
                  longMessageCollapseEnabled={longMessageCollapseEnabled}
                  longMessageCollapseTarget={longMessageCollapseTarget}
                  onOpenMessageModal={openMessageModal}
                  onRegenerateMessage={regenerateMessage}
                />
              </View>
            );
          }}
          ListEmptyComponent={() => (
            <View style={styles.emptyContainer}>
              <MaterialCommunityIcons name="lightning-bolt-outline" size={54} color="#ffffff" style={{ marginBottom: 12 }} />
              <Text style={styles.emptyText}>كيف يمكنني مساعدتك اليوم؟</Text>
            </View>
          )}
        />

        {/* زر الرجوع للأسفل */}
        {showScrollToBottom && (
          <TouchableOpacity style={styles.scrollToBottomButton} onPress={scrollToBottom}>
            <Ionicons name="chevron-down" size={24} color="#000000" />
          </TouchableOpacity>
        )}

        {/* حقل الإدخال مع المرفقات */}
        <View style={styles.inputWrapper}>
          {/* صف المرفقات في الأسفل */}
          {pendingFiles.length > 0 && (
            <View style={styles.attachmentBar}>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flex: 1 }}>
                {pendingFiles.map(file => (
                  <View key={file.id} style={styles.attachmentChip}>
                    {file.type === 'image' ? (
                      <Image source={{ uri: file.uri }} style={styles.previewThumb} />
                    ) : (
                      <Ionicons name="document-outline" size={18} color="#ffffff" />
                    )}
                    <Text style={styles.attachmentChipText} numberOfLines={1}>{file.name}</Text>
                    <TouchableOpacity onPress={() => removeFile(file.id)} style={{ marginLeft: 4 }}>
                      <Ionicons name="close-circle" size={18} color="#aaaaaa" />
                    </TouchableOpacity>
                  </View>
                ))}
              </ScrollView>
              <TouchableOpacity style={styles.attachAddButton} onPress={pickAndUploadFile}>
                <Ionicons name="add-circle-outline" size={24} color="#ffffff" />
              </TouchableOpacity>
            </View>
          )}

          {/* حقل النص وزر الإرسال - الموسع ليسع سطرين */}
          <View style={styles.inputContainer}>
            {/* السطر الأول: TextInput + زر الإرسال */}
            <View style={styles.inputRow}>
              <TextInput
                style={styles.input}
                placeholder="اكتب هنا سؤالك..."
                placeholderTextColor="#666666"
                value={inputText}
                onChangeText={setInputText}
                multiline
              />
              <View style={styles.inputActions}>
                <Animated.View style={{ transform: [{ scale: sendButtonScale }] }}>
                  <TouchableOpacity
                    style={[styles.sendButton, !inputText.trim() && pendingFiles.length === 0 && styles.sendButtonDisabled]}
                    onPress={() => {
                      animateSendButton();
                      handleSendMessage();
                    }}
                    disabled={(!inputText.trim() && pendingFiles.length === 0) || isLoading}
                  >
                    <Ionicons name="arrow-up" size={20} color="#000000" />
                  </TouchableOpacity>
                </Animated.View>
              </View>
            </View>

            {/* السطر الثاني: أزرار الأوضاع والمرفقات */}
            <View style={styles.controlsRow}>
              {/* زر إرفاق ملفات (يمين) */}
              <TouchableOpacity style={styles.controlButton} onPress={pickAndUploadFile}>
                <Ionicons name="attach-outline" size={22} color="#ffffff" />
              </TouchableOpacity>

              {/* زر البحث (يسار) */}
              <TouchableOpacity style={styles.controlButton} onPress={() => setGlobalSearchEnabled(!globalSearchEnabled)}>
                <Ionicons 
                  name={globalSearchEnabled ? "globe" : "globe-outline"} 
                  size={22} 
                  color={globalSearchEnabled ? "#3b82f6" : "#666666"} 
                />
              </TouchableOpacity>

              {/* زر التفكير (يسار) */}
              <TouchableOpacity style={styles.controlButton} onPress={() => setThinkingEnabled(!thinkingEnabled)}>
                <Ionicons 
                  name={thinkingEnabled ? "bulb" : "bulb-outline"} 
                  size={22} 
                  color={thinkingEnabled ? "#eab308" : "#666666"} 
                />
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </KeyboardAvoidingView>

      {/* مودال عرض الصورة بالحجم الكامل */}
      <Modal
        visible={!!fullImageUri}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setFullImageUri(null)}
      >
        <View style={styles.fullImageOverlay}>
          <TouchableOpacity style={styles.fullImageClose} onPress={() => setFullImageUri(null)}>
            <Ionicons name="close" size={32} color="#ffffff" />
          </TouchableOpacity>
          <Image
            source={{ uri: fullImageUri }}
            style={styles.fullImage}
            resizeMode="contain"
          />
        </View>
      </Modal>

      {/* مودال عرض مصادر البحث المتقدمة */}
      <Modal
        visible={sourcesModalVisible}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setSourcesModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>المصادر المكتشفة</Text>
              <TouchableOpacity onPress={() => setSourcesModalVisible(false)}>
                <Ionicons name="close-circle-outline" size={26} color="#ffffff" />
              </TouchableOpacity>
            </View>
            <ScrollView style={styles.modalBody}>
              {selectedSources.map((source, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={styles.sourceItem}
                  onPress={() => Linking.openURL(source.url)}
                >
                  <View style={styles.sourceHeader}>
                    {source.site_icon ? (
                      <Image source={{ uri: source.site_icon }} style={styles.sourceIcon} />
                    ) : (
                      <Ionicons name="link-outline" size={14} color="#ffffff" style={{ marginRight: 6 }} />
                    )}
                    <Text style={styles.sourceSiteName}>{source.site_name || 'رابط ويب'}</Text>
                  </View>
                  <Text style={styles.sourceTitle}>{source.title}</Text>
                  <Text style={styles.sourceSnippet} numberOfLines={2}>{source.snippet}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* شاشة الإعدادات العالمية الممتازة */}
      <Modal
        visible={settingsModalVisible}
        transparent={true}
        animationType="slide"
        onRequestClose={() => setSettingsModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>الإعدادات العامة</Text>
              <TouchableOpacity onPress={() => setSettingsModalVisible(false)}>
                <Ionicons name="close" size={26} color="#ffffff" />
              </TouchableOpacity>
            </View>
            <View style={styles.modalBody}>
              <View style={styles.settingsGroup}>
                <Text style={styles.settingsLabel}>الواجهة والتجربة</Text>
                <View style={styles.settingsRow}>
                  <Text style={styles.settingsText}>المظهر المظلم الحالك</Text>
                  <Ionicons name="moon" size={20} color="#ffffff" />
                </View>
                <View style={styles.settingsRow}>
                  <Text style={styles.settingsText}>إصدار التطبيق العالي</Text>
                  <Text style={{ color: '#666666', fontSize: 14 }}>v2.5.0 Premium</Text>
                </View>
              </View>

              <View style={styles.settingsGroup}>
                <Text style={styles.settingsLabel}>الرسائل والفقاعات</Text>
                <View style={styles.settingsRow}>
                  <Text style={styles.settingsText}>حجم خط الفقاعات</Text>
                  <View style={styles.fontSizeControls}>
                    <TouchableOpacity style={styles.fontSizeButton} onPress={() => setBubbleFontSize(prev => Math.max(12, prev - 1))}>
                      <Ionicons name="remove" size={18} color="#ffffff" />
                    </TouchableOpacity>
                    <Text style={styles.fontSizeValue}>{bubbleFontSize}</Text>
                    <TouchableOpacity style={styles.fontSizeButton} onPress={() => setBubbleFontSize(prev => Math.min(26, prev + 1))}>
                      <Ionicons name="add" size={18} color="#ffffff" />
                    </TouchableOpacity>
                  </View>
                </View>
                <View style={styles.settingsRow}>
                  <Text style={styles.settingsText}>اختصار الرسائل الطويلة</Text>
                  <Switch
                    value={longMessageCollapseEnabled}
                    onValueChange={setLongMessageCollapseEnabled}
                    trackColor={{ false: '#222222', true: '#ffffff' }}
                    thumbColor={longMessageCollapseEnabled ? '#000000' : '#666666'}
                  />
                </View>
                {longMessageCollapseEnabled && (
                  <View style={styles.collapseTargetContainer}>
                    <TouchableOpacity
                      style={[styles.collapseTargetButton, longMessageCollapseTarget === 'user' && styles.activeCollapseTargetButton]}
                      onPress={() => setLongMessageCollapseTarget('user')}
                    >
                      <Text style={[styles.collapseTargetButtonText, longMessageCollapseTarget === 'user' && styles.activeCollapseTargetButtonText]}>رسائل المستخدم</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.collapseTargetButton, longMessageCollapseTarget === 'all' && styles.activeCollapseTargetButton]}
                      onPress={() => setLongMessageCollapseTarget('all')}
                    >
                      <Text style={[styles.collapseTargetButtonText, longMessageCollapseTarget === 'all' && styles.activeCollapseTargetButtonText]}>المستخدم والذكاء الاصطناعي</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[styles.collapseTargetButton, longMessageCollapseTarget === 'ai' && styles.activeCollapseTargetButton]}
                      onPress={() => setLongMessageCollapseTarget('ai')}
                    >
                      <Text style={[styles.collapseTargetButtonText, longMessageCollapseTarget === 'ai' && styles.activeCollapseTargetButtonText]}>الذكاء الاصطناعي</Text>
                    </TouchableOpacity>
                  </View>
                )}
              </View>

              <TouchableOpacity style={styles.dangerButton} onPress={clearAllChats}>
                <Ionicons name="trash-outline" size={20} color="#ff3b30" style={{ marginLeft: 8 }} />
                <Text style={styles.dangerButtonText}>حذف جميع سجلات المحادثات</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* مودال تحديد ونسخ الرسالة عند الضغط المطول - في المنتصف */}
      <Modal
        visible={messageModalVisible}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setMessageModalVisible(false)}
      >
        <View style={styles.messageModalOverlay}>
          <View style={styles.messageModalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>تحديد الرسالة ونسخها</Text>
              <TouchableOpacity onPress={() => setMessageModalVisible(false)}>
                <Ionicons name="close" size={26} color="#ffffff" />
              </TouchableOpacity>
            </View>
            <TextInput
              style={styles.selectableMessageInput}
              value={selectedMessage?.text || ''}
              multiline
              editable={true}
              selectTextOnFocus={true}
              textAlignVertical="top"
              placeholderTextColor="#666666"
            />
            <View style={styles.messageModalActions}>
              <TouchableOpacity style={styles.primaryModalButton} onPress={copySelectedMessage}>
                <Ionicons name={messageModalCopied ? "checkmark-outline" : "copy-outline"} size={18} color="#000000" style={{ marginLeft: 8 }} />
                <Text style={styles.primaryModalButtonText}>{messageModalCopied ? 'تم النسخ' : 'نسخ الرسالة بالكامل'}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* مودال تعديل اسم المحادثة */}
      <Modal
        visible={renameModalVisible}
        transparent={true}
        animationType="fade"
        onRequestClose={() => setRenameModalVisible(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.renameModalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>تعديل اسم المحادثة</Text>
              <TouchableOpacity onPress={() => setRenameModalVisible(false)}>
                <Ionicons name="close" size={26} color="#ffffff" />
              </TouchableOpacity>
            </View>
            <View style={styles.modalBody}>
              <TextInput
                style={styles.renameInput}
                value={renameText}
                onChangeText={setRenameText}
                placeholder="اسم المحادثة"
                placeholderTextColor="#666666"
              />
              <TouchableOpacity style={styles.primaryModalButton} onPress={saveRenamedChat}>
                <Ionicons name="save-outline" size={18} color="#000000" style={{ marginLeft: 8 }} />
                <Text style={styles.primaryModalButtonText}>حفظ الاسم</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* القائمة الجانبية المستوحاة من تطبيقات الذكاء الاصطناعي */}
      {isSidebarOpen && (
        <TouchableOpacity style={styles.overlay} activeOpacity={1} onPress={toggleSidebar} />
      )}

      <Animated.View style={[styles.sidebar, { transform: [{ translateX: sidebarAnim }] }]}>
        <SafeAreaView style={{ flex: 1 }}>
          <View style={styles.sidebarHeader}>
            <Text style={styles.sidebarTitle}>السجلات التاريخية</Text>
            <TouchableOpacity onPress={toggleSidebar}>
              <Ionicons name="chevron-forward-outline" size={24} color="#ffffff" />
            </TouchableOpacity>
          </View>

          <TouchableOpacity style={styles.newChatSidebarButton} onPress={createNewChat}>
            <Ionicons name="add-outline" size={20} color="#000000" style={{ marginLeft: 8 }} />
            <Text style={styles.newChatSidebarButtonText}>محادثة جديدة بالكامل</Text>
          </TouchableOpacity>

          <FlatList
            data={sortedChats}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <View style={[styles.chatListItem, item.id === currentChatId && styles.activeChatItem]}>
                <TouchableOpacity style={styles.chatSelectArea} onPress={() => selectChat(item.id)}>
                  <Ionicons
                    name={item.pinned ? "pin" : "chatbox-ellipses-outline"}
                    size={18}
                    color={item.id === currentChatId ? "#ffffff" : "#666666"}
                    style={{ marginLeft: 10 }}
                  />
                  <Text style={[styles.chatListTitle, item.id === currentChatId && styles.activeChatListTitle]} numberOfLines={1}>
                    {item.title}
                  </Text>
                </TouchableOpacity>
                <View style={styles.chatItemActions}>
                  <TouchableOpacity style={styles.chatActionIcon} onPress={() => togglePinChat(item.id)}>
                    <Ionicons name={item.pinned ? "pin" : "pin-outline"} size={16} color={item.pinned ? "#ffffff" : "#666666"} />
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.chatActionIcon} onPress={() => openRenameChat(item)}>
                    <Ionicons name="pencil-outline" size={16} color="#666666" />
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.chatActionIcon} onPress={() => deleteChat(item.id)}>
                    <Ionicons name="trash-outline" size={16} color="#ff3b30" />
                  </TouchableOpacity>
                </View>
              </View>
            )}
          />

          {/* زر ترقية الإعدادات في القائمة الجانبية السفلى */}
          <TouchableOpacity style={styles.sidebarSettingsButton} onPress={() => { setSettingsModalVisible(true); setIsSidebarOpen(false); }}>
            <Ionicons name="settings-outline" size={20} color="#ffffff" style={{ marginLeft: 10 }} />
            <Text style={{ color: '#ffffff', fontSize: 14, fontWeight: '500' }}>التفضيلات والإعدادات</Text>
          </TouchableOpacity>
        </SafeAreaView>
      </Animated.View>
    </SafeAreaView>
  );
}

// ================= الأنماط الفاخرة للجيل الجديد (Pitch Black Theme) =================
const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000000',
  },
  header: {
    height: 56,
    flexDirection: 'row-reverse',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    borderBottomWidth: 0.5,
    borderBottomColor: '#111111',
    backgroundColor: '#000000',
  },
  headerTitle: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '600',
    textAlign: 'center',
    flex: 1,
    marginHorizontal: 12,
  },
  iconButton: {
    padding: 6,
  },
  messagesList: {
    paddingVertical: 16,
    paddingHorizontal: 14,
  },
  messageRow: {
    flexDirection: 'row-reverse',
    marginVertical: 10,
    alignItems: 'flex-start',
    width: '100%',
  },
  userRow: {
    justifyContent: 'flex-start',
  },
  aiRow: {
    justifyContent: 'flex-end',
  },
  bubble: {
    maxWidth: '92%',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 18,
  },
  userBubble: {
    backgroundColor: '#161616',
    alignSelf: 'flex-start',
  },
  aiBubble: {
    backgroundColor: '#000000',
    alignSelf: 'flex-end',
    paddingHorizontal: 2,
  },
  messageText: {
    color: '#ffffff',
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'right',
  },
  // صندوق التفكير
  thinkingContainer: {
    backgroundColor: '#1a1a1a',
    borderRadius: 12,
    marginBottom: 8,
    maxWidth: '92%',
    alignSelf: 'flex-end',
    borderWidth: 0.5,
    borderColor: '#333',
    overflow: 'hidden',
  },
  thinkingHeader: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 10,
    backgroundColor: '#111',
  },
  thinkingHeaderText: {
    color: '#aaa',
    fontSize: 13,
    fontWeight: '600',
    marginHorizontal: 8,
    flex: 1,
    textAlign: 'right',
  },
  thinkingBody: {
    padding: 12,
  },
  thinkingText: {
    color: '#aaa',
    fontSize: 14,
    lineHeight: 20,
    textAlign: 'right',
  },
  // مرفقات المستخدم فوق الفقاعة
  userAttachmentsContainer: {
    flexDirection: 'row-reverse',
    flexWrap: 'wrap',
    marginBottom: 8,
    maxWidth: '92%',
  },
  userImageAttachment: {
    marginLeft: 8,
    marginBottom: 8,
    borderRadius: 8,
    overflow: 'hidden',
    width: 60,
    height: 60,
  },
  userImageThumb: {
    width: '100%',
    height: '100%',
  },
  userFileAttachment: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    backgroundColor: '#1a1a1a',
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginLeft: 8,
    marginBottom: 8,
    borderWidth: 0.5,
    borderColor: '#333333',
  },
  userFileName: {
    color: '#ffffff',
    fontSize: 13,
    marginRight: 6,
    maxWidth: 100,
  },
  // معاينة مصغرة في شريط المرفقات أعلى الإدخال
  previewThumb: {
    width: 24,
    height: 24,
    borderRadius: 4,
    marginRight: 6,
  },
  // زر الرجوع للأسفل
  scrollToBottomButton: {
    position: 'absolute',
    bottom: 150,
    right: 20,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#ffffff',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
    elevation: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.4,
    shadowRadius: 4,
  },
  // أنماط ماركداون المخصصة
  blockquote: {
    backgroundColor: '#111111',
    borderLeftColor: '#333333',
    borderLeftWidth: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginVertical: 6,
    borderRadius: 4,
  },
  inlineCode: {
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    backgroundColor: '#1a1a1a',
    color: '#ffffff',
    paddingHorizontal: 5,
    paddingVertical: 2,
    borderRadius: 4,
    fontSize: 14,
  },
  listItemRow: {
    flexDirection: 'row-reverse',
    alignItems: 'flex-start',
    marginBottom: 4,
  },
  bulletPoint: {
    color: '#ffffff',
    marginRight: 6,
    fontSize: 16,
    lineHeight: 22,
  },
  // خط أفقي
  hr: {
    borderBottomColor: '#333333',
    borderBottomWidth: 1,
    marginVertical: 12,
  },
  // جداول
  tableContainer: {
    marginVertical: 8,
    borderWidth: 1,
    borderColor: '#333333',
    borderRadius: 4,
    overflow: 'hidden',
  },
  tableRow: {
    flexDirection: 'row',
  },
  tableCell: {
    borderColor: '#333333',
    borderWidth: 0.5,
    padding: 8,
    minWidth: 80,
  },
  tableHeaderCell: {
    backgroundColor: '#1a1a1a',
  },
  // صورة ماركداون
  markdownImage: {
    width: width * 0.75,
    height: 200,
    borderRadius: 8,
    marginVertical: 6,
    alignSelf: 'center',
  },
  // أنماط الكود البرمجي
  codeBlockWrapper: {
    backgroundColor: '#111111',
    borderRadius: 12,
    marginVertical: 8,
    width: '100%',
    borderWidth: 0.5,
    borderColor: '#222222',
    overflow: 'hidden',
  },
  codeBlockHeader: {
    flexDirection: 'row-reverse',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#1a1a1a',
    borderBottomWidth: 0.5,
    borderBottomColor: '#222222',
  },
  codeLanguage: {
    color: '#aaaaaa',
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  codeCopyButton: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    backgroundColor: '#2a2a2a',
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 10,
  },
  codeCopyButtonText: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: '700',
    marginHorizontal: 5,
  },
  codeScroll: {
    padding: 14,
  },
  codeBlockText: {
    color: '#dddddd',
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    fontSize: 13,
    textAlign: 'left',
  },
  // باقي الأنماط
  typingIndicator: {
    padding: 4,
    marginTop: 4,
  },
  emptyContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 180,
  },
  emptyText: {
    color: '#ffffff',
    fontSize: 17,
    fontWeight: '500',
    textAlign: 'center',
  },
  inputWrapper: {
    backgroundColor: '#000000',
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderTopWidth: 0.5,
    borderTopColor: '#111111',
  },
  attachmentBar: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    marginBottom: 10,
    minHeight: 40,
    paddingHorizontal: 4,
  },
  attachmentChip: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    backgroundColor: '#1a1a1a',
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginLeft: 8,
    borderWidth: 0.5,
    borderColor: '#333333',
  },
  attachmentChipText: {
    color: '#ffffff',
    fontSize: 13,
    maxWidth: 120,
    marginRight: 4,
  },
  attachAddButton: {
    padding: 6,
  },
  // الحاوية الرئيسية للإدخال (موسعة لتشمل سطرين)
  inputContainer: {
    backgroundColor: '#111111',
    borderRadius: 26,
    borderWidth: 0.5,
    borderColor: '#222222',
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  // صف الإدخال العلوي (نص + زر إرسال)
  inputRow: {
    flexDirection: 'row-reverse',
    alignItems: 'flex-end',
  },
  input: {
    flex: 1,
    color: '#ffffff',
    fontSize: 15,
    textAlign: 'right',
    maxHeight: 120,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  inputActions: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    marginBottom: 2,
  },
  sendButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#ffffff',
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendButtonDisabled: {
    backgroundColor: '#222222',
  },
  // صف الأزرار السفلية (أوضاع + مرفقات)
  controlsRow: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
    paddingTop: 6,
    borderTopWidth: 0.5,
    borderTopColor: '#222222',
  },
  controlButton: {
    padding: 8,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  // مودال عرض الصورة كاملة
  fullImageOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.95)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  fullImageClose: {
    position: 'absolute',
    top: 50,
    right: 20,
    zIndex: 1,
  },
  fullImage: {
    width: '90%',
    height: '80%',
    borderRadius: 12,
  },
  overlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.85)',
    zIndex: 99,
  },
  sidebar: {
    position: 'absolute',
    top: 0,
    bottom: 0,
    right: 0,
    width: SIDEBAR_WIDTH,
    backgroundColor: '#000000',
    zIndex: 100,
    padding: 16,
    borderLeftWidth: 0.5,
    borderLeftColor: '#111111',
  },
  sidebarHeader: {
    flexDirection: 'row-reverse',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
    paddingTop: Platform.OS === 'ios' ? 10 : 24,
  },
  sidebarTitle: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '700',
  },
  newChatSidebarButton: {
    flexDirection: 'row-reverse',
    backgroundColor: '#ffffff',
    padding: 12,
    borderRadius: 24,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 20,
  },
  newChatSidebarButtonText: {
    color: '#000000',
    fontSize: 14,
    fontWeight: '700',
  },
  chatListItem: {
    flexDirection: 'row-reverse',
    padding: 12,
    borderRadius: 12,
    alignItems: 'center',
    marginBottom: 6,
  },
  activeChatItem: {
    backgroundColor: '#111111',
  },
  chatListTitle: {
    color: '#666666',
    fontSize: 14,
    flex: 1,
    textAlign: 'right',
  },
  activeChatListTitle: {
    color: '#ffffff',
    fontWeight: '600',
  },
  sidebarSettingsButton: {
    flexDirection: 'row-reverse',
    paddingVertical: 14,
    borderTopWidth: 0.5,
    borderTopColor: '#111111',
    alignItems: 'center',
  },
  sourcesButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#111111',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 0.5,
    borderColor: '#222222',
    marginTop: 6,
    alignSelf: 'flex-start',
  },
  sourcesButtonText: {
    color: '#ffffff',
    fontSize: 12,
    fontWeight: '500',
    marginHorizontal: 4,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.9)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#050505',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    maxHeight: '80%',
    paddingBottom: 30,
    borderWidth: 0.5,
    borderColor: '#111111',
  },
  modalHeader: {
    flexDirection: 'row-reverse',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 0.5,
    borderBottomColor: '#111111',
  },
  modalTitle: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: '700',
  },
  modalBody: {
    padding: 20,
  },
  sourceItem: {
    backgroundColor: '#111111',
    padding: 14,
    borderRadius: 14,
    marginBottom: 10,
    borderWidth: 0.5,
    borderColor: '#222222',
  },
  sourceHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 6,
  },
  sourceIcon: {
    width: 14,
    height: 14,
    marginRight: 6,
    borderRadius: 7,
  },
  sourceSiteName: {
    color: '#666666',
    fontSize: 12,
  },
  sourceTitle: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 4,
  },
  sourceSnippet: {
    color: '#666666',
    fontSize: 12,
    lineHeight: 18,
    textAlign: 'right',
  },
  settingsGroup: {
    backgroundColor: '#111111',
    borderRadius: 16,
    padding: 16,
    marginBottom: 20,
    borderWidth: 0.5,
    borderColor: '#222222',
  },
  settingsLabel: {
    color: '#666666',
    fontSize: 12,
    fontWeight: '700',
    marginBottom: 12,
    textTransform: 'uppercase',
  },
  settingsRow: {
    flexDirection: 'row-reverse',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 12,
  },
  settingsText: {
    color: '#ffffff',
    fontSize: 15,
  },
  fontSizeControls: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
  },
  fontSizeButton: {
    width: 30,
    height: 30,
    borderRadius: 15,
    backgroundColor: '#222222',
    alignItems: 'center',
    justifyContent: 'center',
  },
  fontSizeValue: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '700',
    marginHorizontal: 12,
    minWidth: 22,
    textAlign: 'center',
  },
  collapseTargetContainer: {
    marginTop: 8,
  },
  collapseTargetButton: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 12,
    backgroundColor: '#050505',
    borderWidth: 0.5,
    borderColor: '#222222',
    marginBottom: 8,
  },
  activeCollapseTargetButton: {
    backgroundColor: '#ffffff',
    borderColor: '#ffffff',
  },
  collapseTargetButtonText: {
    color: '#ffffff',
    fontSize: 13,
    textAlign: 'right',
    fontWeight: '600',
  },
  activeCollapseTargetButtonText: {
    color: '#000000',
  },
  collapsedMessageContent: {
    maxHeight: 104,
    overflow: 'hidden',
  },
  expandMessageButton: {
    marginTop: 8,
    paddingVertical: 9,
    paddingHorizontal: 12,
    borderRadius: 10,
    backgroundColor: '#ffffff',
    alignItems: 'center',
    justifyContent: 'center',
  },
  expandMessageButtonText: {
    color: '#000000',
    fontSize: 13,
    fontWeight: '700',
  },
  messageActions: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    marginTop: 6,
  },
  userMessageActions: {
    alignSelf: 'flex-start',
  },
  aiMessageActions: {
    alignSelf: 'flex-end',
  },
  messageActionButton: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: 6,
    backgroundColor: 'transparent',
  },
  messageModalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.9)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
  },
  messageModalContent: {
    width: '100%',
    maxHeight: '80%',
    backgroundColor: '#050505',
    borderRadius: 24,
    borderWidth: 0.5,
    borderColor: '#111111',
    paddingBottom: 24,
  },
  selectableMessageInput: {
    margin: 20,
    minHeight: 220,
    maxHeight: 420,
    color: '#ffffff',
    backgroundColor: '#111111',
    borderRadius: 16,
    borderWidth: 0.5,
    borderColor: '#222222',
    padding: 14,
    fontSize: 15,
    lineHeight: 23,
    textAlign: 'right',
  },
  messageModalActions: {
    paddingHorizontal: 20,
  },
  primaryModalButton: {
    flexDirection: 'row-reverse',
    backgroundColor: '#ffffff',
    padding: 14,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryModalButtonText: {
    color: '#000000',
    fontSize: 14,
    fontWeight: '800',
  },
  renameModalContent: {
    backgroundColor: '#050505',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingBottom: 24,
    borderWidth: 0.5,
    borderColor: '#111111',
  },
  renameInput: {
    color: '#ffffff',
    backgroundColor: '#111111',
    borderRadius: 14,
    borderWidth: 0.5,
    borderColor: '#222222',
    padding: 14,
    fontSize: 15,
    textAlign: 'right',
    marginBottom: 16,
  },
  chatSelectArea: {
    flex: 1,
    flexDirection: 'row-reverse',
    alignItems: 'center',
  },
  chatItemActions: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    marginRight: 6,
  },
  chatActionIcon: {
    padding: 5,
    marginHorizontal: 1,
  },
  dangerButton: {
    flexDirection: 'row-reverse',
    backgroundColor: '#1a0505',
    padding: 14,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 0.5,
    borderColor: '#4a0f0f',
  },
  dangerButtonText: {
    color: '#ff3b30',
    fontSize: 14,
    fontWeight: '700',
  },
});